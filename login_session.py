"""Run this ONCE on your own computer to log in the userbot account.
It prints a USER_SESSION string; paste it into your host's environment variables.
Keep it secret: anyone with it can use that Telegram account."""
import os

from dotenv import load_dotenv
from telethon.sessions import StringSession
from telethon.sync import TelegramClient

load_dotenv()
api_id = int(os.getenv("API_ID") or input("API_ID: "))
api_hash = os.getenv("API_HASH") or input("API_HASH: ")

with TelegramClient(StringSession(), api_id, api_hash) as client:
    print("\nLogged in as:", client.get_me().first_name)
    print("\n===== USER_SESSION (copy everything on the next line) =====\n")
    print(client.session.save())
    print("\n===========================================================")
