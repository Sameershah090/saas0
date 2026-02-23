import os
import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

from telethon import TelegramClient, events
from telethon.tl.types import MessageMediaPhoto, MessageMediaDocument
from playwright.async_api import async_playwright
import qrcode
from PIL import Image
import io
import re
from typing import Dict, List, Optional
import tempfile

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('whatsapp_forwarder.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class WhatsAppTelegramForwarder:
    def __init__(self):
        # Telegram configuration
        self.telegram_bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
        self.telegram_chat_id = int(os.getenv('TELEGRAM_CHAT_ID'))
        
        # WhatsApp configuration
        self.whatsapp_session_file = os.getenv('WHATSAPP_SESSION_FILE', 'session.json')
        
        # Security limits
        self.max_message_length = int(os.getenv('MAX_MESSAGE_LENGTH', 4096))
        self.max_media_size = self.parse_size(os.getenv('MAX_MEDIA_SIZE', '20MB'))
        
        # WhatsApp client
        self.browser = None
        self.page = None
        
        # Telegram client
        self.telegram_client = None
        
        # Track contacts and messages
        self.contacts = {}
        self.message_history = {}
        self.chat_groups = {}  # Group messages by contact
        self.active_chats = []  # Track active chats
        
    def parse_size(self, size_str: str) -> int:
        """Parse size string to bytes"""
        size_str = size_str.upper()
        if size_str.endswith('KB'):
            return int(size_str[:-2]) * 1024
        elif size_str.endswith('MB'):
            return int(size_str[:-2]) * 1024 * 1024
        elif size_str.endswith('GB'):
            return int(size_str[:-2]) * 1024 * 1024 * 1024
        else:
            return int(size_str)  # Assume bytes

    async def initialize_telegram(self):
        """Initialize Telegram bot client"""
        if not self.telegram_bot_token:
            raise ValueError("TELEGRAM_BOT_TOKEN not found in environment variables")
        
        self.telegram_client = TelegramClient('whatsapp_forwarder_bot', 0, '', loop=asyncio.get_event_loop())
        await self.telegram_client.start(bot_token=self.telegram_bot_token)
        
        logger.info("Telegram bot initialized")

    async def generate_qr_code(self):
        """Generate QR code for WhatsApp Web login and send to Telegram"""
        try:
            # Create a temporary page to get the QR code
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await browser.new_page()
                
                # Navigate to WhatsApp Web
                await page.goto('https://web.whatsapp.com/')
                
                # Wait for QR code to appear
                qr_selector = 'canvas[data-ref]'
                await page.wait_for_selector(qr_selector, timeout=30000)
                
                # Get the QR code data
                qr_data = await page.evaluate(f'''
                    () => {{
                        const canvas = document.querySelector('{qr_selector}');
                        return canvas.toDataURL();
                    }}
                ''')
                
                # Convert base64 to image
                import base64
                header, encoded = qr_data.split(',', 1)
                decoded_data = base64.b64decode(encoded)
                
                # Save QR code as image
                qr_image_path = 'whatsapp_login_qr.png'
                with open(qr_image_path, 'wb') as f:
                    f.write(decoded_data)
                
                # Send QR code to Telegram
                await self.send_qr_to_telegram(qr_image_path)
                
                logger.info("QR code generated and sent to Telegram")
                
                # Keep browser open for login detection
                # We'll close it once login is detected
                self.login_browser = browser
                self.login_page = page
                
                # Monitor for successful login
                await self.monitor_login_success()
                
        except Exception as e:
            logger.error(f"Error getting QR code: {e}")
            raise

    async def monitor_login_success(self):
        """Monitor for successful login after QR scan"""
        try:
            # Wait for main chat panel to appear (indicating successful login)
            await self.login_page.wait_for_selector('div[data-testid="chat-list-panel"]', timeout=60000)
            logger.info("Successfully logged into WhatsApp Web")
            
            # Close the login browser
            await self.login_browser.close()
            
            # Now start the main message listener
            asyncio.create_task(self.setup_whatsapp_listener())
            
        except Exception as e:
            logger.error(f"Error during login monitoring: {e}")
            await self.login_browser.close()

    async def send_qr_to_telegram(self, qr_image_path):
        """Send QR code image to Telegram"""
        try:
            await self.telegram_client.send_file(
                self.telegram_chat_id,
                qr_image_path,
                caption="Scan this QR code with your WhatsApp to log in."
            )
            logger.info("QR code sent to Telegram")
        except Exception as e:
            logger.error(f"Error sending QR code to Telegram: {e}")

    async def setup_whatsapp_listener(self):
        """Setup WhatsApp Web listener using Playwright"""
        try:
            # If we already have a browser (from login), use that
            if hasattr(self, 'browser') and self.browser:
                self.page = await self.browser.new_page()
            else:
                async with async_playwright() as p:
                    self.browser = await p.chromium.launch(headless=False)  # Set to False for debugging
                    self.page = await self.browser.new_page()
            
            # Navigate to WhatsApp Web
            await self.page.goto('https://web.whatsapp.com/')
            
            # Wait for main interface to load
            await self.page.wait_for_selector('div[data-testid="chat-list-panel"]', timeout=30000)
            logger.info("WhatsApp Web loaded successfully")
            
            # Start listening for messages
            await self.listen_for_messages()
            
        except Exception as e:
            logger.error(f"Error setting up WhatsApp listener: {e}")
            raise

    async def listen_for_messages(self):
        """Listen for incoming and outgoing WhatsApp messages"""
        # Inject JavaScript to monitor messages
        await self.page.evaluate('''
            // Store previously processed message IDs
            window.processedMessages = new Set();
            
            // Function to get current chat info
            function getCurrentChatInfo() {
                const chatTitleElement = document.querySelector('[data-testid="conversation-info-header"] [dir="auto"]');
                const chatTitle = chatTitleElement ? chatTitleElement.textContent : 'Unknown Chat';
                return {
                    title: chatTitle,
                    url: window.location.href
                };
            }
            
            // Function to monitor new messages
            function monitorMessages() {
                const observer = new MutationObserver((mutations) => {
                    mutations.forEach((mutation) => {
                        mutation.addedNodes.forEach((node) => {
                            if (node.nodeType === 1) { // Element node
                                // Check if it's a new message
                                if (node.querySelector && node.querySelector('[data-testid="msg"]')) {
                                    const messageElement = node.querySelector('[data-testid="msg"]');
                                    if (messageElement) {
                                        const messageId = messageElement.getAttribute('data-id');
                                        
                                        // Skip if already processed
                                        if (window.processedMessages.has(messageId)) {
                                            return;
                                        }
                                        window.processedMessages.add(messageId);
                                        
                                        // Extract message information
                                        const senderElement = node.closest('[data-testid="conversation-panel-body"]')
                                            .querySelector('[title]');
                                        
                                        // Determine if it's sent or received
                                        const isOutgoing = messageElement.classList.contains('message-out');
                                        
                                        // Get message text
                                        let messageText = '';
                                        const textElement = messageElement.querySelector('[dir="auto"]');
                                        if (textElement) {
                                            messageText = textElement.textContent || textElement.innerText;
                                        } else {
                                            // Check for other possible text containers
                                            const messageContainer = messageElement.querySelector('.copyable-text');
                                            if (messageContainer) {
                                                messageText = messageContainer.textContent || messageContainer.innerText;
                                            }
                                        }
                                        
                                        // Check for media
                                        const mediaElement = messageElement.querySelector('img, video, audio');
                                        const hasMedia = !!mediaElement;
                                        
                                        const senderName = senderElement ? senderElement.title : 'Unknown';
                                        const chatInfo = getCurrentChatInfo();
                                        
                                        // Send message data to Python
                                        window.pywebview.api.on_new_message({
                                            id: messageId,
                                            text: messageText,
                                            sender: senderName,
                                            chat: chatInfo.title,
                                            is_outgoing: isOutgoing,
                                            has_media: hasMedia,
                                            timestamp: new Date().toISOString()
                                        });
                                    }
                                }
                            }
                        });
                    });
                });
                
                // Observe the chat panel for new messages
                const chatPanel = document.querySelector('[data-testid="conversation-panel-body"]');
                if (chatPanel) {
                    observer.observe(chatPanel, {
                        childList: true,
                        subtree: true
                    });
                }
            }
            
            // Start monitoring
            monitorMessages();
        ''')

        # Continuously check for messages
        while True:
            try:
                # Wait for messages to be processed via the API
                await asyncio.sleep(1)
                
            except Exception as e:
                logger.error(f"Error in message listener: {e}")
                await asyncio.sleep(10)  # Wait before retrying

    async def process_message(self, message_data):
        """Process incoming WhatsApp message and forward to Telegram"""
        message_id = message_data.get('id', '')
        sender = message_data.get('sender', 'Unknown')
        text = message_data.get('text', '')
        chat_title = message_data.get('chat', 'Unknown Chat')
        is_outgoing = message_data.get('is_outgoing', False)
        has_media = message_data.get('has_media', False)
        media_url = message_data.get('media_url', None)
        timestamp = message_data.get('timestamp', datetime.now().isoformat())
        
        # Get contact info
        contact_info = await self.get_contact_info(sender)
        
        # Determine message direction
        direction = "📤 Sent" if is_outgoing else "📥 Received"
        
        # Handle media messages
        if has_media and media_url:
            try:
                # Download and send media to Telegram
                await self.download_and_send_media(
                    media_url, 
                    contact_info, 
                    direction, 
                    chat_title, 
                    timestamp
                )
            except Exception as e:
                logger.error(f"Error processing media message: {e}")
        
        # Handle text messages
        if text:
            # Format message for Telegram
            formatted_msg = (
                f"{direction} WhatsApp Message\n"
                f"👤 Contact: {contact_info['display_name']} ({contact_info['phone_number']})\n"
                f"💬 Chat: {chat_title}\n"
                f"📝 Message: {text}\n"
                f"🕒 Time: {timestamp}"
            )
            
            # Add to message history
            if contact_info['id'] not in self.message_history:
                self.message_history[contact_info['id']] = []
            self.message_history[contact_info['id']].append({
                'id': message_id,
                'text': text,
                'direction': 'outgoing' if is_outgoing else 'incoming',
                'timestamp': timestamp,
                'chat': chat_title
            })
            
            # Add to chat groups
            if contact_info['id'] not in self.chat_groups:
                self.chat_groups[contact_info['id']] = []
            self.chat_groups[contact_info['id']].append(formatted_msg)
            
            # Update active chats
            if contact_info['id'] not in self.active_chats:
                self.active_chats.append(contact_info['id'])
            
            # Send to Telegram
            try:
                await self.telegram_client.send_message(
                    self.telegram_chat_id,
                    formatted_msg
                )
                logger.info(f"Message forwarded to Telegram: {direction} - {sender} - {text[:50]}...")
            except Exception as e:
                logger.error(f"Error forwarding message to Telegram: {e}")
    
    async def download_and_send_media(self, media_url: str, contact_info: dict, direction: str, chat_title: str, timestamp: str):
        """Download media from WhatsApp and send to Telegram"""
        try:
            import aiohttp
            import os
            
            # Create a temporary file for the media
            _, ext = os.path.splitext(media_url)
            temp_filename = f"temp_media_{int(datetime.now().timestamp())}{ext}"
            
            # Download the media
            async with aiohttp.ClientSession() as session:
                async with session.get(media_url) as resp:
                    if resp.status == 200:
                        with open(temp_filename, 'wb') as f:
                            async for chunk in resp.content.iter_chunked(8192):
                                f.write(chunk)
                        
                        # Prepare caption
                        caption = (
                            f"{direction} WhatsApp Media\n"
                            f"👤 Contact: {contact_info['display_name']} ({contact_info['phone_number']})\n"
                            f"💬 Chat: {chat_title}\n"
                            f"🕒 Time: {timestamp}"
                        )
                        
                        # Send to Telegram
                        await self.telegram_client.send_file(
                            self.telegram_chat_id,
                            temp_filename,
                            caption=caption
                        )
                        
                        logger.info(f"Media forwarded to Telegram: {direction} - {contact_info['display_name']}")
                        
                        # Clean up the temporary file
                        os.remove(temp_filename)
                    else:
                        logger.error(f"Failed to download media: HTTP {resp.status}")
        except Exception as e:
            logger.error(f"Error downloading and sending media: {e}")

    async def get_contact_info(self, sender):
        """Get contact information (name and phone number)"""
        # In a real implementation, this would look up contact details from WhatsApp
        # For now, we'll extract phone number and name from the sender string
        if sender in self.contacts:
            return self.contacts[sender]
        
        # Try to parse phone number and name
        # WhatsApp often shows names in format like "Name (Phone Number)" or just phone numbers
        phone_pattern = r'\+(\d{1,3}[\s-]?\d{4,14})'
        phone_match = re.search(phone_pattern, sender)
        
        if phone_match:
            phone_number = phone_match.group(1).replace(' ', '').replace('-', '')
            display_name = sender.replace(f'({phone_match.group(0)})', '').strip()
            if display_name == '':
                display_name = sender
        else:
            # If no phone number pattern found, treat the whole string as name
            # and create a placeholder phone number
            display_name = sender
            phone_number = f"unknown_{len(self.contacts)+1}"
        
        contact_info = {
            'display_name': display_name,
            'phone_number': phone_number,
            'id': phone_number  # Use phone number as ID
        }
        
        self.contacts[sender] = contact_info
        return contact_info

    async def handle_telegram_commands(self):
        """Handle Telegram bot commands"""
        @self.telegram_client.on(events.NewMessage(pattern='/start'))
        async def start_handler(event):
            await event.respond(
                "Welcome to WhatsApp-Telegram Forwarder!\n"
                "Use /login to get a QR code to log into WhatsApp Web."
            )

        @self.telegram_client.on(events.NewMessage(pattern='/login'))
        async def login_handler(event):
            await event.respond("Generating QR code for WhatsApp Web login...")
            await self.generate_qr_code()

        @self.telegram_client.on(events.NewMessage(pattern='/help'))
        async def help_handler(event):
            help_text = (
                "WhatsApp-Telegram Forwarder Commands:\n"
                "/start - Start the bot\n"
                "/login - Get QR code to log into WhatsApp Web\n"
                "/help - Show this help message\n"
                "/chats - List recent chats\n"
                "/search [name] - Search messages from a specific contact\n"
                "/history [contact] - Get message history with a contact\n"
                "/contacts - List all contacts\n"
                "/stats - Get statistics"
            )
            await event.respond(help_text)

        @self.telegram_client.on(events.NewMessage(pattern='/chats'))
        async def chats_handler(event):
            if not self.active_chats:
                await event.respond("No active chats yet.")
                return
            
            chat_list = "Recent Chats:\n"
            for i, contact_id in enumerate(self.active_chats[-10:], 1):  # Last 10 chats
                contact_info = self.contacts.get(contact_id, {'display_name': contact_id})
                chat_list += f"{i}. {contact_info['display_name']} ({contact_info['phone_number']})\n"
            
            await event.respond(chat_list)

        @self.telegram_client.on(events.NewMessage(pattern='/contacts'))
        async def contacts_handler(event):
            if not self.contacts:
                await event.respond("No contacts yet.")
                return
            
            contact_list = "All Contacts:\n"
            for i, (contact_id, contact_info) in enumerate(self.contacts.items(), 1):
                contact_list += f"{i}. {contact_info['display_name']} ({contact_info['phone_number']})\n"
            
            await event.respond(contact_list)

        @self.telegram_client.on(events.NewMessage(pattern=r'/search (.+)'))
        async def search_handler(event):
            name = event.pattern_match.group(1).lower()
            matching_contacts = []
            
            for contact_id, contact_info in self.contacts.items():
                if name in contact_info['display_name'].lower() or name in contact_info['phone_number']:
                    matching_contacts.append(contact_info)
            
            if matching_contacts:
                result = f"Found {len(matching_contacts)} contact(s) matching '{name}':\n"
                for i, contact in enumerate(matching_contacts, 1):
                    result += f"{i}. {contact['display_name']} ({contact['phone_number']})\n"
            else:
                result = f"No contacts found matching '{name}'"
            
            await event.respond(result)

        @self.telegram_client.on(events.NewMessage(pattern=r'/history (.+)'))
        async def history_handler(event):
            contact_name = event.pattern_match.group(1)
            
            # Find the contact
            target_contact_id = None
            for contact_id, contact_info in self.contacts.items():
                if (contact_name.lower() in contact_info['display_name'].lower() or 
                    contact_name in contact_info['phone_number']):
                    target_contact_id = contact_id
                    break
            
            if not target_contact_id or target_contact_id not in self.message_history:
                await event.respond(f"No message history found for: {contact_name}")
                return
            
            # Get last 10 messages
            messages = self.message_history[target_contact_id][-10:]
            history_text = f"Last 10 messages with {self.contacts[target_contact_id]['display_name']}:\n\n"
            
            for msg in messages:
                direction_icon = "📤" if msg['direction'] == 'outgoing' else "📥"
                history_text += f"{direction_icon} {msg['text']} ({msg['timestamp']})\n\n"
            
            # Split long messages if needed
            if len(history_text) > self.max_message_length:
                # Send in chunks
                chunks = [history_text[i:i+self.max_message_length] for i in range(0, len(history_text), self.max_message_length)]
                for chunk in chunks:
                    await event.respond(chunk)
            else:
                await event.respond(history_text)

        @self.telegram_client.on(events.NewMessage(pattern='/stats'))
        async def stats_handler(event):
            stats_text = (
                f"📊 WhatsApp-Telegram Forwarder Stats:\n"
                f"Active Chats: {len(self.active_chats)}\n"
                f"Total Contacts: {len(self.contacts)}\n"
                f"Messages Processed: {sum(len(msgs) for msgs in self.message_history.values())}\n"
                f"Status: {'✅ Active' if hasattr(self, 'page') and self.page else '❌ Not Connected'}"
            )
            await event.respond(stats_text)

    async def run(self):
        """Run the WhatsApp-Telegram forwarder"""
        try:
            # Initialize Telegram bot
            await self.initialize_telegram()
            
            # Setup command handlers
            await self.handle_telegram_commands()
            
            # Start WhatsApp listener in background
            whatsapp_task = asyncio.create_task(self.setup_whatsapp_listener())
            
            # Run Telegram bot forever
            await self.telegram_client.run_until_disconnected()
            
        except Exception as e:
            logger.error(f"Error running WhatsApp-Telegram forwarder: {e}")
            raise


# Main execution
if __name__ == "__main__":
    forwarder = WhatsAppTelegramForwarder()
    
    try:
        asyncio.run(forwarder.run())
    except KeyboardInterrupt:
        logger.info("Shutting down WhatsApp-Telegram forwarder...")
    except Exception as e:
        logger.error(f"Critical error: {e}")