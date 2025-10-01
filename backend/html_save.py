from seleniumbase import Driver
import time

def final_bypass_test(url):
    """
    Attempts to bypass the CAPTCHA by using a real Chrome user profile.
    """
    # ===================== FIX THIS LINE =====================
    # Paste your REAL profile path from chrome://version here.
    # Make sure to remove \Default from the end.
    profile_path = r"C:\Users\proka\AppData\Local\Google\Chrome\User Data"
    # =========================================================

    # This MUST be run in visible mode (headless=False).
    # Make sure your regular Chrome browser is COMPLETELY CLOSED before running.
    driver = Driver(uc=True, headless=False, user_data_dir=profile_path)

    try:
        print("Attempting to open page with your real Chrome profile...")
        driver.get(url)

        print("\nPage has loaded. Please observe the browser window.")
        print("The CAPTCHA should not appear. You should see the car listings.")
        
        # We will pause here so you can see the result.
        input("Press Enter in this terminal to close the browser...")

    except Exception as e:
        print(f"An error occurred: {e}")
    finally:
        driver.quit()

# --- Main execution block ---
if __name__ == "__main__":
    target_url = "https://www.sahibinden.com/bmw-3-serisi?sorting=date_desc"
    final_bypass_test(target_url)