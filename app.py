import streamlit as st
import pandas as pd
import requests
import time
import os
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
import logging

logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ---------- Session State Initialization ----------
if "authenticated" not in st.session_state:
    st.session_state.authenticated = False
if "uploaded_df" not in st.session_state:
    st.session_state.uploaded_df = None

# ---------- Configuration ----------
AUTH_KEY = os.getenv("APP_AUTH_KEY", "demo-secret")
WHATSAPP_TOKEN = os.getenv("WHATSAPP_ACCESS_TOKEN", "")
WHATSAPP_PHONE_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")

# ---------- Authentication ----------
def authenticate():
    st.title("WhatsApp Bulk Messenger")
    st.subheader("Authentication")
    api_key = st.text_input("Enter API Key", type="password")
    if st.button("Submit"):
        if api_key == AUTH_KEY:
            st.session_state.authenticated = True
            st.success("Authentication successful!")
            st.rerun()
        else:
            st.error("Invalid API key. Please try again.")

# ---------- File Validation ----------
def validate_file(uploaded_file) -> tuple:
    try:
        if uploaded_file.name.endswith(".csv"):
            df = pd.read_csv(uploaded_file)
        elif uploaded_file.name.endswith((".xls", ".xlsx")):
            df = pd.read_excel(uploaded_file, engine="openpyxl")
        else:
            return None, "Unsupported file format. Please upload CSV or Excel."
    except Exception as e:
        return None, f"Error reading file: {e}"

    required_cols = {"Name", "PhoneNumber", "Message"}
    if not required_cols.issubset(df.columns):
        missing = required_cols - set(df.columns)
        return None, f"Missing columns: {', '.join(missing)}"

    df["PhoneNumber"] = df["PhoneNumber"].astype(str).str.strip()
    df["Message"] = df["Message"].astype(str).str.strip()
    df["Name"] = df["Name"].astype(str).str.strip()
    return df, None

# ---------- WhatsApp Cloud API ----------
def send_via_api(df, token, phone_number_id):
    url = f"https://graph.facebook.com/v18.0/{phone_number_id}/messages"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    results = {"success": 0, "failed": 0, "details": []}
    progress_bar = st.progress(0)
    total = len(df)

    for idx, row in df.iterrows():
        payload = {
            "messaging_product": "whatsapp",
            "to": row["PhoneNumber"],
            "type": "text",
            "text": {"body": row["Message"]}
        }
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=10)
            if resp.status_code == 200:
                results["success"] += 1
                results["details"].append(f"{row['Name']} ({row['PhoneNumber']}): Sent")
            else:
                results["failed"] += 1
                results["details"].append(
                    f"{row['Name']} ({row['PhoneNumber']}): Failed - "
                    f"{resp.json().get('error', {}).get('message', 'Unknown error')}"
                )
        except Exception as e:
            results["failed"] += 1
            results["details"].append(f"{row['Name']} ({row['PhoneNumber']}): Exception - {str(e)}")

        progress_bar.progress((idx + 1) / total)
        time.sleep(0.2)

    return results

# ---------- Pop-up Dismissal ----------
def dismiss_popup(driver):
    logger.info("=== dismiss_popup called ===")
    logger.info(f"Current URL: {driver.current_url}")

    # Log dialogs found
    try:
        dialogs = driver.find_elements(By.XPATH, "//*[@role='dialog']")
        logger.info(f"Found {len(dialogs)} dialog(s)")
        for i, d in enumerate(dialogs):
            logger.info(f"  Dialog {i}: tag={d.tag_name}, class={d.get_attribute('class')[:80]}")
    except Exception as e:
        logger.error(f"Error finding dialogs: {e}")

    # Log first 10 buttons
    try:
        buttons = driver.find_elements(By.TAG_NAME, "button")
        logger.info(f"Found {len(buttons)} button(s)")
        for i, btn in enumerate(buttons[:10]):
            label = btn.get_attribute("aria-label") or ""
            text = (btn.text or "")[:40]
            logger.info(f"  Button {i}: aria-label='{label}', text='{text}'")
    except Exception as e:
        logger.error(f"Error listing buttons: {e}")

    # Strategy 1: aria-label='Close' (confirmed present in WA Web)
    logger.info("Trying Strategy 1: button[@aria-label='Close']")
    try:
        btn = WebDriverWait(driver, 4).until(
            EC.element_to_be_clickable((By.XPATH, "//button[@aria-label='Close']"))
        )
        logger.info("  Found! Clicking...")
        btn.click()
        time.sleep(1)
        logger.info("  Dismissed via aria-label=Close")
        return
    except Exception as e:
        logger.warning(f"  Strategy 1 failed: {e}")

    # Strategy 2: any button inside role=dialog
    logger.info("Trying Strategy 2: button inside role=dialog")
    try:
        btn = WebDriverWait(driver, 4).until(
            EC.element_to_be_clickable((By.XPATH, "//*[@role='dialog']//button"))
        )
        logger.info(f"  Found: aria-label='{btn.get_attribute('aria-label')}' text='{btn.text}'")
        btn.click()
        time.sleep(1)
        logger.info("  Dismissed via dialog button")
        return
    except Exception as e:
        logger.warning(f"  Strategy 2 failed: {e}")

    # Strategy 3: span data-icon='x'
    logger.info("Trying Strategy 3: span[@data-icon='x']")
    try:
        btn = WebDriverWait(driver, 3).until(
            EC.element_to_be_clickable((By.XPATH, "//span[@data-icon='x']"))
        )
        btn.click()
        time.sleep(1)
        logger.info("  Dismissed via data-icon=x")
        return
    except Exception as e:
        logger.warning(f"  Strategy 3 failed: {e}")

    # Strategy 4: JS remove all dialogs
    logger.info("Trying Strategy 4: JS remove dialogs")
    try:
        removed = driver.execute_script("""
            var count = 0;
            document.querySelectorAll('[role="dialog"]').forEach(el => { el.remove(); count++; });
            return count;
        """)
        logger.info(f"  JS removed {removed} dialog(s)")
    except Exception as e:
        logger.error(f"  Strategy 4 failed: {e}")

# ---------- Selenium Sender ----------
def send_via_selenium(df):
    results = {"success": 0, "failed": 0, "details": []}

    try:
        options = webdriver.ChromeOptions()
        options.add_argument("--start-maximized")
        driver = webdriver.Chrome(
            service=Service(ChromeDriverManager().install()),
            options=options
        )
        logger.info("Chrome driver started successfully")
    except Exception as e:
        logger.error(f"Chrome driver failed: {e}")
        st.error(f"Failed to start Chrome driver: {e}")
        return results

    driver.get("https://web.whatsapp.com")
    logger.info("Navigated to web.whatsapp.com")
    st.info("Please scan the QR code in the opened browser window. Waiting for login...")

    # ✅ FIXED: Use confirmed working selectors from browser console output
    LOGIN_XPATHS = [
        "//input[@title='Search or start a new chat']",   # ✅ confirmed: INPUT found
        "//div[@id='pane-side']",                          # ✅ confirmed: DIV found
        "//div[@aria-label='Chat list']",                  # ✅ confirmed: DIV found
        "//button[@aria-label='New chat']",                # ✅ confirmed: BUTTON found
    ]

    logger.info("Waiting for login indicator...")
    start = time.time()
    logged_in = False

    while time.time() - start < 120:
        for xpath in LOGIN_XPATHS:
            try:
                el = driver.find_element(By.XPATH, xpath)
                if el:
                    logged_in = True
                    logger.info(f"✅ Login detected via: {xpath}")
                    break
            except:
                continue

        if logged_in:
            break

        try:
            logger.info(f"URL={driver.current_url} | Title={driver.title} | Elapsed={int(time.time()-start)}s")
        except:
            pass

        logger.info(f"Not logged in yet... ({int(time.time()-start)}s elapsed)")
        time.sleep(5)

    if not logged_in:
        logger.error("Login timeout after 120s")
        st.error("Timed out waiting for WhatsApp Web login.")
        driver.quit()
        return results

    st.success("Login successful!")
    logger.info("Sleeping 4s before dismissing popup...")
    time.sleep(4)
    dismiss_popup(driver)
    time.sleep(1)

    total = len(df)
    progress_bar = st.progress(0)

    for idx, row in df.iterrows():
        name = row["Name"]
        phone = row["PhoneNumber"]
        message = row["Message"]

        logger.info(f"--- Sending to {name} ({phone}) ---")

        try:
            url = f"https://web.whatsapp.com/send?phone={phone}&text=&app_absent=0"
            driver.get(url)
            logger.info(f"Navigated to: {url}")

            # ✅ FIXED: Updated input box selectors
            input_box = None
            for xpath in [
                "//div[@aria-label='Type a message']",
                "//div[@title='Type a message']",
                "//footer//div[@contenteditable='true']",
                "//div[@contenteditable='true'][@data-tab='10']",
                "//div[@contenteditable='true'][@data-tab='6']",
            ]:
                try:
                    input_box = WebDriverWait(driver, 15).until(
                        EC.element_to_be_clickable((By.XPATH, xpath))
                    )
                    logger.info(f"✅ Input box found: {xpath}")
                    break
                except:
                    logger.warning(f"  XPath not found: {xpath}")

            if input_box is None:
                raise Exception("Message input box not found — number may be invalid or not on WhatsApp")

            dismiss_popup(driver)
            input_box.click()
            input_box.send_keys(message)
            time.sleep(0.5)
            input_box.send_keys(Keys.ENTER)
            logger.info(f"✅ Message sent to {name}")

            results["success"] += 1
            results["details"].append(f"{name} ({phone}): Sent")
            time.sleep(2)

        except Exception as e:
            logger.error(f"❌ Failed for {name} ({phone}): {e}")
            results["failed"] += 1
            results["details"].append(f"{name} ({phone}): Failed - {str(e)}")

        progress_bar.progress((idx + 1) / total)

    driver.quit()
    return results

# ---------- Main App ----------
def main():
    if not st.session_state.authenticated:
        authenticate()
        return

    st.sidebar.title("WhatsApp Bulk Messenger")
    st.sidebar.markdown("Upload your contacts and send messages.")

    with st.sidebar.expander("WhatsApp API Credentials (Optional)"):
        token = st.text_input("Access Token", value=WHATSAPP_TOKEN, type="password")
        phone_id = st.text_input("Phone Number ID", value=WHATSAPP_PHONE_ID)
        if token and phone_id:
            st.success("Using WhatsApp Cloud API")
            use_api = True
        else:
            st.info("Will fall back to Selenium + WhatsApp Web")
            use_api = False

    uploaded_file = st.file_uploader("Choose a CSV or Excel file", type=["csv", "xlsx", "xls"])

    if uploaded_file is not None:
        df, error = validate_file(uploaded_file)
        if error:
            st.error(error)
            st.session_state.uploaded_df = None
        else:
            st.session_state.uploaded_df = df
            st.success("File validated successfully!")
            st.dataframe(df)

    if st.session_state.uploaded_df is not None:
        if st.button("Send WhatsApp Messages", type="primary"):
            df = st.session_state.uploaded_df
            results = None

            if use_api and token and phone_id:
                st.info("Sending via WhatsApp Cloud API...")
                results = send_via_api(df, token, phone_id)
            else:
                st.info("Launching WhatsApp Web via Selenium...")
                results = send_via_selenium(df)

            if results:
                st.subheader("Sending Results")
                st.write(f"✅ Success: {results['success']}  |  ❌ Failed: {results['failed']}")
                with st.expander("Detailed Log"):
                    for detail in results["details"]:
                        st.text(detail)

if __name__ == "__main__":
    main()