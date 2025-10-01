import json
import time
from collections import OrderedDict
from seleniumbase import Driver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException

# --- CONFIGURATION ---
# The root categories to scrape, mapped to their desired output filenames
ROOT_CATEGORIES = {
    "elektrikli-otomobil": "https://www.sahibinden.com/otomobil/elektrikli",
    "elektrikli-arazi-suv-pickup": "https://www.sahibinden.com/arazi-suv-pickup/elektrikli",
}

def handle_page_challenges(driver):
    """
    Waits for the main content to load, handling potential CAPTCHA checks.
    """
    try:
        driver.uc_gui_click_captcha()
    except Exception:
        pass  # No CAPTCHA found, which is fine.

    print("Waiting for page content to load...")
    try:
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, '#searchResultLeft-category, ul.category-list')
            )
        )
        print("Page content loaded.")
    except TimeoutException:
        print("WARNING: Page content did not load after 15 seconds. Scraping may fail.")
    time.sleep(0.5)

def get_all_options_from_list(driver, item_selector):
    """
    Extracts all options from a list, intelligently deciding whether to scroll
    based on the presence of a scrollbar.
    """
    options = OrderedDict()
    scroll_container_selector = '#searchCategoryContainer'
    content_pane_selector = f'{scroll_container_selector} .jspPane'

    try:
        wait = WebDriverWait(driver, 10)
        scroll_container = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, scroll_container_selector)))
        
        # --- ROBUSTNESS FIX ---
        # First, always scrape the initially visible items. This handles all short lists.
        initial_elements = driver.find_elements(By.CSS_SELECTOR, f"{content_pane_selector} {item_selector} a")
        for element in initial_elements:
            name = element.text.strip().split('\n')[0]
            url = element.get_attribute('href')
            if name and url and name not in options:
                options[name] = {'url': url}

        # Now, check if a scrollbar element exists. If not, we are done.
        try:
            # The custom scrollbar is only present if the list is long enough to scroll.
            driver.find_element(By.CSS_SELECTOR, f'{scroll_container_selector} .jspVerticalBar')
            print("Scrollbar detected. Using PAGE_DOWN key press for scrolling.")
        except NoSuchElementException:
            print("No scrollbar detected. List is short, scraping complete.")
            print(f"Successfully scraped {len(options)} items.")
            return options # Exit early as there's nothing to scroll

        # If we reach here, a scrollbar exists, so proceed with the scrolling loop.
        last_option_count = -1
        retries = 3

        while retries > 0:
            # Scrape all items again to catch any newly loaded ones
            elements = driver.find_elements(By.CSS_SELECTOR, f"{content_pane_selector} {item_selector} a")
            for element in elements:
                name = element.text.strip().split('\n')[0]
                url = element.get_attribute('href')
                if name and url and name not in options:
                    options[name] = {'url': url}

            if len(options) == last_option_count:
                retries -= 1
                print(f"Scroll did not reveal new items. Retries left: {retries}")
                time.sleep(1)
            else:
                retries = 3

            last_option_count = len(options)
            
            if retries == 0:
                print("No new items found after multiple retries. Assuming end of list.")
                break

            print(f"Scrolling down... Current option count: {len(options)}")
            scroll_container.send_keys(Keys.PAGE_DOWN)
            time.sleep(0.8)

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
    Main function to scrape each category into its own separate JSON file.
    """
    driver = None
    try:
        driver = Driver(uc=True, headless=False)

        # --- Loop through each category and scrape it independently ---
        for category_name, category_url in ROOT_CATEGORIES.items():
            output_filename = f"{category_name.replace('-', '_')}.json"
            print(f"\n\n{'='*60}")
            print(f"STARTING SCRAPE FOR CATEGORY: '{category_name}'")
            print(f"Data will be saved to: '{output_filename}'")
            print(f"{'='*60}\n")
            
            category_data = OrderedDict()

            # 1. Get all brands for the current category
            print(f"\n--- Scraping Brands from: {category_url} ---")
            driver.uc_open_with_reconnect(category_url, 4)
            driver.click_if_visible("button#onetrust-accept-btn-handler")
            handle_page_challenges(driver)
            
            brands = get_all_options_from_list(driver, 'li.cl2')
            category_data.update(brands)
            
            # 2. Scrape details for each brand within THIS category
            for brand_name, brand_data in category_data.items():
                print(f"\n--- Processing Brand: {brand_name} ---")
                driver.uc_open_with_reconnect(brand_data['url'], 4)
                handle_page_challenges(driver)
                
                series = get_all_options_from_list(driver, 'li.cl3')
                brand_data['series'] = series
                print(f"Found {len(series)} series for {brand_name}.")

                for series_name, series_data in series.items():
                    print(f"  -> Processing Series: {series_name}")
                    driver.uc_open_with_reconnect(series_data['url'], 4)
                    handle_page_challenges(driver)
                    
                    models = get_all_options_from_list(driver, 'li.cl4')
                    series_data['models'] = models
                    print(f"    Found {len(models)} models for {series_name}.")

                    for model_name, model_data in models.items():
                        print(f"      >> Processing Model: {model_name}")
                        driver.uc_open_with_reconnect(model_data['url'], 4)
                        handle_page_challenges(driver)

                        packets_list = get_all_options_from_list(driver, 'li.cl5')
                        model_data['packets'] = list(sorted(packets_list.keys()))
                        print(f"        Found {len(packets_list)} packets for {model_name}.")

            # 3. Save the completed data for this category to its file
            print(f"\n--- FINISHED CATEGORY: '{category_name}'. Saving data to '{output_filename}'... ---")
            with open(output_filename, 'w', encoding='utf-8') as f:
                json.dump(category_data, f, ensure_ascii=False, indent=4)
            print("Save complete.")

    except Exception as e:
        print(f"\nAN UNEXPECTED ERROR OCCURRED: {e}")
    finally:
        print("\nAll scraping runs finished.")
        if driver:
            driver.quit()

if __name__ == "__main__":
    main()