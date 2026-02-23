# WhatsApp-Telegram Forwarder

A Python application that forwards WhatsApp messages to Telegram. The application uses Playwright to interact with WhatsApp Web and Telethon to communicate with Telegram.

## Features

- ✅ Forward WhatsApp messages to Telegram
- ✅ Display sender's name and phone number
- ✅ Organize chats by contact
- ✅ Support for both incoming and outgoing messages
- ✅ Media support (images, videos, documents)
- ✅ Message history tracking
- ✅ Telegram bot commands for managing the application
- ✅ QR code login to WhatsApp Web

## Requirements

- Python 3.8+
- Node.js and npm (for Playwright)

## Installation

1. Clone the repository:
```bash
git clone <repository-url>
cd whatsapp-telegram-forwarder
```

2. Install Python dependencies:
```bash
pip install -r requirements.txt
```

3. Install Playwright browsers:
```bash
playwright install chromium
```

4. Set up your Telegram bot:
   - Create a Telegram bot using [@BotFather](https://t.me/BotFather)
   - Get your bot token
   - Get your Telegram chat ID

5. Configure the application:
```bash
cp .env.example .env
```
Then edit `.env` with your credentials.

## Configuration

Create a `.env` file with the following variables:

```env
# Telegram Bot Configuration
TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here
TELEGRAM_CHAT_ID=your_telegram_chat_id_here

# WhatsApp Web Configuration
WHATSAPP_SESSION_FILE=session.json

# Security
MAX_MESSAGE_LENGTH=4096
MAX_MEDIA_SIZE=20MB
```

## Usage

1. Run the application:
```bash
python main.py
```

2. Use Telegram bot commands:
   - `/start` - Start the bot
   - `/login` - Get QR code to log into WhatsApp Web
   - `/help` - Show available commands
   - `/chats` - List recent chats
   - `/contacts` - List all contacts
   - `/search [name]` - Search messages from a specific contact
   - `/history [contact]` - Get message history with a contact
   - `/stats` - Get application statistics

3. Scan the QR code sent to your Telegram bot with your WhatsApp mobile app to log in.

## Architecture

The application consists of:

- **WhatsApp Listener**: Uses Playwright to interact with WhatsApp Web and detect new messages
- **Telegram Bot**: Uses Telethon to send messages to Telegram and handle commands
- **Message Processor**: Handles message formatting and storage
- **Media Handler**: Downloads and forwards media files

## Security Considerations

- Store your Telegram bot token securely
- The application stores WhatsApp session data locally
- Messages are temporarily stored in memory
- Validate all inputs from external sources

## Troubleshooting

- If the QR code doesn't appear, check your internet connection
- Make sure your Telegram bot is properly configured
- Check the logs for error messages
- Ensure WhatsApp Web is accessible from your network

## Known Issues

- Media files might not download properly due to WhatsApp's security measures
- Some message types might not be captured correctly
- The application needs to run continuously to capture messages

## Contributing

Pull requests are welcome. For major changes, please open an issue first to discuss what you would like to change.

## License

This project is licensed under the MIT License.