import imaplib
import email
import datetime
from dotenv import load_dotenv

from config import EMAIL_ADDRESS, PASSWORD

# Load credentials from .env file
load_dotenv()

# Connect to Gmail using IMAP
imap = imaplib.IMAP4_SSL("imap.gmail.com")
imap.login(EMAIL_ADDRESS, PASSWORD)
imap.select("inbox")

# Search for emails from "info@test.com" with attachments
search_query = (
    '(FROM "info@test.com" SUBJECT "attachment" SINCE "'
    + (datetime.datetime.now() - datetime.timedelta(hours=24)).strftime("%d-%b-%Y")
    + '")'
)
result, data = imap.search(None, search_query)
latest_email_id = data[0].split()[-1]

# Get the latest email and extract the attachment
result, data = imap.fetch(latest_email_id, "(RFC822)")
email_message = email.message_from_bytes(data[0][1])  # type: ignore
for part in email_message.walk():
    if part.get_content_maintype() == "multipart":
        continue
    if part.get("Content-Disposition") is None:
        continue
    filename = part.get_filename()
    if filename is not None and filename == "CM Lagerbestand.xlsx":
        with open(filename, "wb") as f:
            f.write(part.get_payload(decode=True))  # type: ignore
            print(f"Saved attachment: {filename}")

# Close the IMAP connection
imap.close()
imap.logout()
