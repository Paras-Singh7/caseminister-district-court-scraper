import logging.config
import os
import re
import time
import uuid
from datetime import datetime

import pytesseract
import requests
from azure.storage.blob import BlobServiceClient
from dotenv import load_dotenv
from PIL import Image
from pymongo import MongoClient
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.select import Select
from selenium.webdriver.support.ui import WebDriverWait

pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

load_dotenv()

# Configuring logger #############################################
LOG_DIR = os.path.join(os.getcwd(), "logs")
if not os.path.exists(LOG_DIR):
    os.makedirs(LOG_DIR)

logging.config.dictConfig(
    {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "verbose": {
                "format": "{levelname} {asctime} {module} {message}",
                "style": "{",
            },
            "simple": {
                "format": "{levelname} {message}",
                "style": "{",
            },
        },
        "handlers": {
            "file_scraper": {
                "level": "DEBUG",
                "class": "logging.FileHandler",
                "filename": os.path.join(LOG_DIR, "scraper.log"),
                "formatter": "verbose",
            },
        },
        "scraper": {
            "authentication": {
                "handlers": ["file_scraper"],
                "level": "DEBUG",
                "propagate": False,
            },
        },
    }
)

logger = logging.getLogger("scraper")

# Setting up mongo db client #####################################
client = MongoClient(os.getenv("MONGO_URI"))

db = client[os.getenv("MONGO_DB_NAME")]
collection = db[os.getenv("MONGO_COLLECTION_NAME")]


date_formate1 = lambda date: datetime.strftime(
    datetime.strptime(date, "%d-%m-%Y"), "%d/%m/%Y"
)


def date_formate2(string: str):
    pattern = r"(\d{1,2})(?:th|st|nd|rd)?\s+([A-Za-z]+)\s+(\d{4})"
    match = re.search(pattern, string)
    if match:
        day = match.group(1)
        month = match.group(2)
        year = match.group(3)
        month_number = datetime.strptime(month, "%B").month
        extracted_date = datetime(
            year=int(year), month=month_number, day=int(day)
        ).date()
        return extracted_date.strftime("%d/%m/%Y")
    else:
        return None


def download_pdf_with_cookies(pdf_url, driver, file_path):
    """
    Download a PDF via requests, copying session cookies from Selenium's driver.
    This ensures the server sees the same authenticated session and doesn't return 404.
    """
    session = requests.Session()
    for cookie in driver.get_cookies():
        session.cookies.set(cookie["name"], cookie["value"])
    resp = session.get(pdf_url, stream=True)
    if resp.status_code == 200:
        with open(file_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1024):
                if chunk:
                    f.write(chunk)
        return True
    else:
        return False


def extract_case_details(driver):
    """
    Extracts case info, downloads the PDFs, and returns a dictionary with all case data.
    """
    case_data = {}
    try:
        time.sleep(2)  # Let the details page load

        # Case details table
        # Case type
        case_data["details"] = {}
        case_data["details"]["case_type"] = driver.find_element(
            By.CSS_SELECTOR, "td[colspan='3'].fw-bold.text-uppercase"
        ).text.strip()

        # Rest of the data
        rows = driver.find_elements(
            By.CSS_SELECTOR, "table.table.case_details_table tr"
        )
        for row in rows:
            cells = row.find_elements(By.TAG_NAME, "td")
            if len(cells) >= 2:
                label = cells[0].text.strip()
                if "Filing Number" in label:
                    case_data["details"]["filing_number"] = cells[1].text.strip()
                    case_data["details"]["filing_date"] = cells[3].text.strip()
                elif "Registration Number" in label:
                    case_data["details"]["registration_number"] = cells[1].text.strip()
                    case_data["details"]["registration_date"] = cells[3].text.strip()
                elif "CNR Number" in label:
                    case_data["details"]["cnr_number"] = (
                        cells[1].text.strip().split()[0]
                    )

        if case_data["details"].get("filing_date", ""):
            case_data["details"]["filing_date"] = date_formate1(
                case_data["details"]["filing_date"]
            )

        if case_data["details"].get("registration_number", ""):
            case_data["details"]["registration_date"] = date_formate1(
                case_data["details"]["registration_date"]
            )

        # Case status table
        case_data["status"] = {}
        status_rows = driver.find_elements(
            By.CSS_SELECTOR, "table.case_status_table tr"
        )
        for row in status_rows:
            cells = row.find_elements(By.TAG_NAME, "td")
            if len(cells) >= 2:
                label = cells[0].text.strip()
                if "First Hearing Date" in label:
                    case_data["status"]["first_hearing_date"] = cells[1].text.strip()
                elif "Decision Date" in label:
                    case_data["status"]["decision_date"] = cells[1].text.strip()
                elif "Case Status" in label:
                    case_data["status"]["case_status"] = cells[1].text.strip()
                elif "Nature of Disposal" in label:
                    case_data["status"]["nature_of_disposal"] = cells[1].text.strip()
                elif "Court Number and Judge" in label:
                    case_data["status"]["court_number"] = cells[1].text.strip()
                elif "Next Hearing Date" in label:
                    case_data["status"]["next_hearing_date"] = cells[1].text.strip()
                elif "Case Stage" in label:
                    case_data["status"]["case_stage"] = cells[1].text.strip()

        if case_data["status"].get("first_hearing_date", ""):
            case_data["status"]["first_hearing_date"] = date_formate2(
                case_data["status"]["first_hearing_date"]
            )

        if case_data["status"].get("decision_date", ""):
            case_data["status"]["decision_date"] = date_formate2(
                case_data["status"]["decision_date"]
            )

        # Petitioner
        try:
            petitioner_table = driver.find_element(
                By.CSS_SELECTOR, "table.Petitioner_Advocate_table"
            )
            case_data["petitioner_details"] = petitioner_table.text.strip()
        except:
            case_data["petitioner_details"] = ""

        # Respondent
        try:
            respondent_table = driver.find_element(
                By.CSS_SELECTOR, "table.Respondent_Advocate_table"
            )
            case_data["respondent_details"] = respondent_table.text.strip()
        except:
            case_data["respondent_details"] = ""

        # Acts table
        try:
            acts_rows = driver.find_elements(By.CSS_SELECTOR, "table.acts_table tr")
            acts = []
            for row in acts_rows[1:]:  # skip header
                cells = row.find_elements(By.TAG_NAME, "td")
                if len(cells) >= 2:
                    act = cells[0].text.strip()
                    section = cells[1].text.strip()
                    acts.append({"name": act, "sections": section})
            case_data["acts"] = acts
        except:
            case_data["acts"] = []

        # Case History
        try:
            history_rows = driver.find_elements(
                By.CSS_SELECTOR, "table.history_table tbody tr"
            )
            case_history = []

            for row in history_rows:
                temp = {}

                # getting value from cells
                cells = row.find_elements(By.TAG_NAME, "td")
                if len(cells) >= 4:
                    temp["judge"] = cells[0].text.strip() if cells[0].text else ""
                    temp["date"] = cells[1].text.strip() if cells[1].text else ""
                    temp["hearing_date"] = (
                        cells[2].text.strip() if cells[2].text else ""
                    )
                    temp["purpose_of_hearing"] = (
                        cells[3].text.strip() if cells[3].text else ""
                    )

                    # getting business details
                    link = cells[1].find_element(By.TAG_NAME, "a")
                    driver.execute_script("arguments[0].click();", link)
                    time.sleep(1)

                    WebDriverWait(driver, 10).until(
                        EC.presence_of_element_located(
                            (By.CSS_SELECTOR, "div#caseBusinessDiv_caseType")
                        )
                    )

                    business_rows = driver.find_elements(
                        By.CSS_SELECTOR,
                        "div#caseBusinessDiv_caseType div center center table tr",
                    )
                    temp2 = {}
                    for row2 in business_rows[1:]:
                        try:
                            cells2 = row2.find_elements(By.TAG_NAME, "td")
                            label = cells2[0].text.strip()
                            if "Business" in label:
                                temp2["business"] = cells2[2].text.strip()
                            elif "Next Purpose" in label:
                                temp2["next_purpose"] = cells2[2].text.strip()
                            elif "Next Hearing Date" in label:
                                temp2["next_hearing_date"] = cells2[2].text.strip()
                            elif "Nature of Disposal" in label:
                                temp2["nature_of_disposal"] = cells2[2].text.strip()
                            elif "Disposal Date" in label:
                                temp2["disposal_date"] = cells2[2].text.strip()
                        except Exception as e:
                            print(e)
                            continue

                    driver.execute_script("back_fun('CScaseType')")

                    WebDriverWait(driver, 10).until(
                        EC.invisibility_of_element_located(
                            (By.ID, "caseBusinessDiv_caseType")
                        )
                    )

                    temp["business_on_date"] = temp2
                    case_history.append(temp)

            case_data["history"] = case_history
        except Exception as e:
            logger.error(f"Exception: {e}")
            case_data["history"] = []

        # Orders
        orders = []
        try:
            order_tables = driver.find_elements(By.CSS_SELECTOR, "table.order_table")
            for table in order_tables:
                rows = table.find_elements(By.TAG_NAME, "tr")[
                    1:
                ]  # skip header if needed
                for row in rows:
                    cells = row.find_elements(By.TAG_NAME, "td")
                    if len(cells) >= 3:
                        order_info = {
                            "date": cells[1].text.strip(),
                            "detail": cells[2].text.strip(),
                        }
                        try:
                            # Click the link to open the modal
                            link = cells[2].find_element(By.TAG_NAME, "a")
                            driver.execute_script("arguments[0].click();", link)
                            time.sleep(1)  # let modal open

                            # Locate <object> with PDF URL
                            modal_body = driver.find_element(By.ID, "modal_order_body")
                            object_tag = modal_body.find_element(By.TAG_NAME, "object")
                            pdf_path = object_tag.get_attribute("data")  # or "src"

                            # If there's a PDF link, rename by date (avoid invalid chars)
                            if pdf_path:
                                # Create a safe filename from the order_date + order_number
                                filename = f"{uuid.uuid4().hex}.pdf"
                                save_path = os.path.join("pdf", filename)

                                is_downloaded = download_pdf_with_cookies(
                                    pdf_path, driver, save_path
                                )
                                if is_downloaded:
                                    new_url = upload_pdf_to_azure(
                                        save_path, case_data["details"]
                                    )
                                    clean_up(save_path)
                                else:
                                    new_url = ""

                                order_info["url"] = new_url
                        except Exception as ex:
                            logger.error(
                                f"Error downloading PDF for order {order_info['order_number']}: {str(ex)}"
                            )
                        finally:
                            # Close the modal
                            try:
                                close_button = WebDriverWait(driver, 5).until(
                                    EC.element_to_be_clickable(
                                        (
                                            By.XPATH,
                                            "//button[contains(text(),'Close') or contains(@class,'btn-close')]",
                                        )
                                    )
                                )
                                close_button.click()
                                time.sleep(1)
                            except Exception:
                                pass

                        orders.append(order_info)
            case_data["orders"] = orders
        except Exception as e:
            logger.error(f"Error processing orders: {str(e)}")

        return case_data

    except Exception as e:
        logger.error(f"Error in extract_case_details: {str(e)}")
        return case_data


def save_to_mongodb(data):
    collection.insert_one(data)


def upload_pdf_to_azure(file_path, details):
    try:
        blob_name = file_path.split("/")[-1]
        blob_service_client = BlobServiceClient.from_connection_string(
            os.getenv("AZURE_CONNECTION_STRING")
        )
        blob_client = blob_service_client.get_blob_client(
            container=os.getenv("AZURE_CONTAINER_NAME"), blob=blob_name
        )
        if blob_client.exists():
            blob_client.delete_blob()
        with open(file_path, "rb") as data:
            blob_client.upload_blob(data, overwrite=True)
        return blob_client.url
    except Exception as e:
        logger.error(
            f"Error while uploading {file_path} of case {details} to azure: {e}"
        )
        return None


def clean_up(path):
    if os.path.exists(path):
        os.remove(path)


# ----------------------- MAIN SCRIPT -----------------------
if __name__ == "__main__":
    state = "Delhi"
    district = "East"
    court_complex = "Karkardooma Court Complex"
    url = "https://services.ecourts.gov.in/"

    # Ensure PDF folder exists
    if not os.path.exists("pdf"):
        os.makedirs("pdf")

    # Initialize the browser
    chrome_options = Options()
    chrome_options.add_argument("--headless=new")

    driver = webdriver.Chrome()
    wait = WebDriverWait(driver, 20)

    case_type_options = [
        "CS (COMM) - CIVIL SUIT (COMMERCIAL)",
        "EX - EXECUTION",
        "MISC DJ - MISC. CASES FOR DJ ADJ",
        "OMP (COMM) - COMMERCIAL ARBITRATION U/S 34",
        "OMP (I)(COMM.) - Commercial Arbitration U/s 9",
    ]

    for case_type_option in case_type_options:
        for button_id in ["radDCT", "radPCT"]:
            try:
                driver.get(url)
                time.sleep(3)

                # Click "Case Status"
                element = wait.until(
                    EC.element_to_be_clickable((By.ID, "leftPaneMenuCS"))
                )
                element.click()
                time.sleep(2)

                # State
                state_dropdown = Select(
                    wait.until(
                        EC.presence_of_element_located((By.ID, "sess_state_code"))
                    )
                )
                state_dropdown.select_by_visible_text(state)
                time.sleep(3)

                # District
                dist_dropdown = Select(
                    wait.until(
                        EC.presence_of_element_located((By.ID, "sess_dist_code"))
                    )
                )
                dist_dropdown.select_by_visible_text(district)
                time.sleep(3)

                # Court complex
                court_dropdown = Select(
                    wait.until(
                        EC.presence_of_element_located((By.ID, "court_complex_code"))
                    )
                )
                court_dropdown.select_by_visible_text(court_complex)
                time.sleep(2)

                # Close any "validateError" modal if present
                try:
                    driver.execute_script("closeModel({modal_id:'validateError'})")
                    time.sleep(2)
                except:
                    pass

                # Case type button
                case_type_button = wait.until(
                    EC.element_to_be_clickable((By.ID, "casetype-tabMenu"))
                )
                case_type_button.click()
                time.sleep(3)

                # Select "CS (COMM) - CIVIL SUIT (COMMERCIAL)"
                case_type_dropdown = Select(
                    wait.until(EC.presence_of_element_located((By.ID, "case_type_2")))
                )
                case_type_dropdown.select_by_visible_text(case_type_option)

                # Year input
                year_input = wait.until(
                    EC.presence_of_element_located((By.ID, "search_year"))
                )
                year_input.clear()
                year_input.send_keys("2024")

                # Close any leftover modal
                try:
                    driver.execute_script("closeModel({modal_id:'validateError'})")
                    time.sleep(2)
                except:
                    pass

                # Select "Disposed" radio button
                disposed_radio_button = wait.until(
                    EC.element_to_be_clickable((By.ID, button_id))
                )
                driver.execute_script("arguments[0].click();", disposed_radio_button)
                time.sleep(2)

                # Solve Captcha
                captcha_image_element = wait.until(
                    EC.presence_of_element_located((By.ID, "captcha_image"))
                )
                captcha_image_element.screenshot("temp.png")
                captcha_image = Image.open("temp.png")
                captcha_text = pytesseract.image_to_string(captcha_image)

                captcha_input = wait.until(
                    EC.presence_of_element_located((By.ID, "ct_captcha_code"))
                )
                captcha_input.clear()
                captcha_input.send_keys(captcha_text)

                # Submit
                driver.execute_script("submitCaseType();")
                time.sleep(5)

                # ------------------ COLLECT ALL CASE LINKS ------------------
                # Instead of just first "View" link, get them all
                total_cases = driver.find_element(
                    By.XPATH, "//div[@id='showList2']/div[2]/a"
                )
                total_cases = total_cases.text.strip().split(":")[-1].strip()
                print(f"Found {total_cases} cases on this page.")

                # Loop over each result
                for i in range(int(total_cases)):
                    # Because going back can stale the references, re-find them each iteration
                    view_buttons = driver.find_elements(By.XPATH, "//a[text()='View']")

                    # Click the i-th "View" link
                    driver.execute_script(
                        "arguments[0].scrollIntoView(true);", view_buttons[i]
                    )
                    time.sleep(1)
                    driver.execute_script("arguments[0].click();", view_buttons[i])
                    time.sleep(2)

                    # Extract details & download PDFs
                    case_data = extract_case_details(driver)

                    case_data["state"] = state
                    case_data["district"] = district
                    case_data["court_complex"] = court_complex

                    save_to_mongodb(case_data)

                    # Go back to results page
                    driver.back()
                    time.sleep(3)  # Let it load before next iteration

                    print(f"Done: {i + 1}/{len(view_buttons)}", end="\r")

                print("\nAll cases processed. Stored in DB.")

            except Exception as e:
                print(f"An error occurred: {str(e)}")

            finally:
                # Cleanup
                if os.path.exists("temp.png"):
                    os.remove("temp.png")
                time.sleep(2)
                driver.quit()
