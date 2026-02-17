# def download():
#     status = {"message": None, "error": None}
#     saved_file = False
#     # if file exists, delete it
#     if os.path.exists("CM Lagerbestand.xlsx"):
#         os.remove("CM Lagerbestand.xlsx")

#     try:
#         # Load credentials from .env file
#         load_dotenv(".env")
#         EMAIL_ADDRESS = os.getenv("EMAIL_ADDRESS")
#         PASSWORD = os.getenv("EMAIL_PASSWORD")

#         # Connect to Gmail using IMAP
#         imap = imaplib.IMAP4_SSL("imap.gmail.com")
#         imap.login(EMAIL_ADDRESS, PASSWORD)
#         imap.select("inbox")

#         # Search for emails from "info@test.com" with attachments
#         search_query = (
#             '(FROM "info@test.com" SUBJECT "attachment" SINCE "'
#             + (datetime.datetime.now() - datetime.timedelta(hours=24)).strftime(
#                 "%d-%b-%Y"
#             )
#             + '")'
#         )
#         result, data = imap.search(None, search_query)
#         latest_email_id = data[0].split()[-1]
#         email_found = len(data[0].split()) > 0
#         # Get the latest email and extract the attachment
#         result, data = imap.fetch(latest_email_id, "(RFC822)")
#         email_message = email.message_from_bytes(data[0][1])
#         for part in email_message.walk():
#             if part.get_content_maintype() == "multipart":
#                 continue
#             if part.get("Content-Disposition") is None:
#                 continue
#             filename = part.get_filename()

#             if filename is not None and filename == "CM Lagerbestand.xlsx":
#                 with open(filename, "wb") as f:
#                     f.write(part.get_payload(decode=True))
#                     saved_file = True
#         if not email_found:
#             status["error"] = "No email found"
#         if saved_file:
#             status["message"] = "File downloaded successfully"
#         else:
#             status["error"] = "File not found"
#         # Close the IMAP connection
#         imap.close()
#         imap.logout()
#     except Exception as ex:
#         template = "er is een {0} opgetreden: {1!r}"
#         status["error"] = template.format(type(ex).__name__, ex.args)
#     return status
