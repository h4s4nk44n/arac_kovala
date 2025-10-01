import json
import time
import random
import os
from collections import OrderedDict
from seleniumbase import Driver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import NoSuchElementException, TimeoutException

BASE_URL = "https://www.sahibinden.com"

def handle_page_challenges(driver):
    """
    Checks for and handles CAPTCHA challenges on the current page.
    """
    driver.uc_gui_click_captcha()
    
    print("Waiting for page content to load after challenge check...")
    try:
        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, '#searchResultLeft-category, #searchResultsTable, ul.category-list')
            )
        )
        print("Page content loaded successfully.")
    except TimeoutException:
        print("Page content did not load after challenge. Scraping may fail.")
    
    time.sleep(1)

def get_all_options_from_list(driver, item_selector):
    """
    Extracts all options from a list, handling both detailed and simplified page views.
    """
    options = OrderedDict()
    scroll_pane_selector = '#searchCategoryContainer .jspPane'

    try:
        wait = WebDriverWait(driver, 10)
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, scroll_pane_selector)))
        print("Detailed filter list found. Using scroll method.")
        
        last_item_count = -1
        while len(options) > last_item_count:
            last_item_count = len(options)
            
            elements = driver.find_elements(By.CSS_SELECTOR, f"{scroll_pane_selector} {item_selector} a")
            for element in elements:
                name = element.text.strip().split('\n')[0]
                url = element.get_attribute('href')
                if name and url and name not in options:
                    options[name] = {'url': url}
            
            try:
                scroll_pane = driver.find_element(By.CSS_SELECTOR, scroll_pane_selector)
                driver.execute_script("arguments[0].style.top = (parseInt(arguments[0].style.top || 0) - 300) + 'px';", scroll_pane)
                time.sleep(0.5)
            except NoSuchElementException:
                break
    except TimeoutException:
        print("Detailed filter list not found. Falling back to simplified category view.")
        simplified_view_selector = "ul.category-list a"
        try:
            elements = driver.find_elements(By.CSS_SELECTOR, simplified_view_selector)
            for element in elements:
                name = element.text.strip().split('\n')[0]
                url = element.get_attribute('href')
                if name and url and name not in options:
                    options[name] = {'url': url}
        except Exception as e:
            print(f"Could not find elements with fallback selectors. Error: {e}")

    print(f"Successfully scraped {len(options)} items.")
    return options

def main():
    """
    Main function to RESUME scraping from an existing car_data3.json file.
    """
    print("Starting the car data RESUME scraper...")
    
    all_car_data = OrderedDict()
    json_file_path = 'car_data3.json'

    # --- THE FIX: Prioritize loading the local file. ---
    if not os.path.exists(json_file_path):
        print(f"ERROR: '{json_file_path}' not found. Please run a full scraper first to generate the initial file.")
        return

    print(f"Loading existing data from '{json_file_path}' to resume.")
    with open(json_file_path, 'r', encoding='utf-8') as f:
        try:
            all_car_data = OrderedDict(json.load(f))
        except json.JSONDecodeError:
            print(f"ERROR: '{json_file_path}' is corrupted or empty. Cannot resume.")
            return

    driver = None
    try:
        driver = Driver(uc=True, headless=False)
        print(f"Loaded {len(all_car_data)} brands. Checking for incomplete entries...")

        # --- MODIFIED: Loop directly through the loaded data ---
        for brand_name, brand_data in all_car_data.items():
            # If 'series' is not empty, this brand is done. Skip it.
            if brand_data.get('series'):
                print(f"--- Skipping Brand: {brand_name} (already has series data) ---")
                continue

            # If we are here, this brand needs to be scraped.
            print(f"\n--- Processing MISSING Brand: {brand_name} ---")
            
            driver.uc_open_with_reconnect(brand_data['url'], 4)
            driver.click_if_visible("button#onetrust-accept-btn-handler")
            handle_page_challenges(driver)
            
            series = get_all_options_from_list(driver, 'li.cl3')
            all_car_data[brand_name]['series'] = series
            print(f"Found {len(series)} series for {brand_name}.")

            for series_name, series_data in series.items():
                print(f"  -> Processing Series: {series_name}")
                driver.uc_open_with_reconnect(series_data['url'], 4)
                driver.click_if_visible("button#onetrust-accept-btn-handler")
                handle_page_challenges(driver)
                
                models = get_all_options_from_list(driver, 'li.cl4')
                all_car_data[brand_name]['series'][series_name]['models'] = models
                print(f"  Found {len(models)} models for {series_name}.")

                for model_name, model_data in models.items():
                    print(f"    >> Processing Model: {model_name}")
                    driver.uc_open_with_reconnect(model_data['url'], 4)
                    driver.click_if_visible("button#onetrust-accept-btn-handler")
                    handle_page_challenges(driver)

                    packets_list = get_all_options_from_list(driver, 'li.cl5')
                    all_car_data[brand_name]['series'][series_name]['models'][model_name]['packets'] = list(sorted(packets_list.keys()))
                    print(f"    Found {len(packets_list)} packets for {model_name}.")

            # Save progress after each brand is fully completed
            print(f"--- Finished Brand: {brand_name}. Saving progress... ---")
            with open(json_file_path, 'w', encoding='utf-8') as f:
                json.dump(all_car_data, f, ensure_ascii=False, indent=4)


    except Exception as e:
        print(f"\nAn unexpected error occurred: {e}")
    finally:
        print("\nScraping run finished. Saving final data...")
        if driver:
            driver.quit()
        if all_car_data:
            with open(json_file_path, 'w', encoding='utf-8') as f:
                json.dump(all_car_data, f, ensure_ascii=False, indent=4)
            print(f"Successfully saved data to '{json_file_path}'!")


if __name__ == "__main__":
    main()