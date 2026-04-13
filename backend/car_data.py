import json
import time
import os
from collections import OrderedDict
from scrapling.fetchers import StealthyFetcher

BASE_URL = "https://www.sahibinden.com"


def _scrape_options_from_page(url, item_selector):
    """
    Fetch a page and extract all options from a category list.
    Uses page_action to handle scrolling for long lists.
    Returns an OrderedDict of {name: {url: ...}}.
    """
    options = OrderedDict()

    def _extract_action(page):
        # Dismiss cookie banner
        try:
            btn = page.query_selector("button#onetrust-accept-btn-handler")
            if btn:
                btn.click()
                page.wait_for_timeout(500)
        except Exception:
            pass

        # Wait for content to load
        try:
            page.wait_for_selector(
                '#searchResultLeft-category, #searchResultsTable, ul.category-list',
                timeout=20000
            )
        except Exception:
            print("Page content did not load after challenge. Scraping may fail.")

        page.wait_for_timeout(1000)

        scroll_pane_selector = '#searchCategoryContainer .jspPane'

        # Check if detailed filter list exists
        scroll_pane = page.query_selector(scroll_pane_selector)

        if scroll_pane:
            # Detailed filter list found - scroll to load all items
            last_item_count = -1
            while True:
                elements = page.query_selector_all(f"{scroll_pane_selector} {item_selector} a")
                for element in elements:
                    name = (element.text_content() or '').strip().split('\n')[0]
                    href = element.get_attribute('href')
                    if name and href and name not in options:
                        options[name] = {'url': href}

                if len(options) == last_item_count:
                    break
                last_item_count = len(options)

                # Scroll by manipulating the pane position
                try:
                    page.evaluate("""(selector) => {
                        const el = document.querySelector(selector);
                        if (el) el.style.top = (parseInt(el.style.top || 0) - 300) + 'px';
                    }""", scroll_pane_selector)
                    page.wait_for_timeout(500)
                except Exception:
                    break
        else:
            # Simplified category view fallback
            print("Detailed filter list not found. Falling back to simplified category view.")
            elements = page.query_selector_all("ul.category-list a")
            for element in elements:
                name = (element.text_content() or '').strip().split('\n')[0]
                href = element.get_attribute('href')
                if name and href and name not in options:
                    options[name] = {'url': href}

        print(f"Successfully scraped {len(options)} items.")

    try:
        StealthyFetcher.fetch(
            url=url,
            headless=False,
            solve_cloudflare=True,
            block_webrtc=True,
            hide_canvas=True,
            network_idle=True,
            timeout=30000,
            page_action=_extract_action,
        )
    except Exception as e:
        print(f"Error fetching {url}: {e}")

    return options


def main():
    """
    Main function to RESUME scraping from an existing car_data3.json file.
    """
    print("Starting the car data RESUME scraper...")

    all_car_data = OrderedDict()
    json_file_path = 'car_data3.json'

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

    print(f"Loaded {len(all_car_data)} brands. Checking for incomplete entries...")

    for brand_name, brand_data in all_car_data.items():
        # If 'series' is not empty, this brand is done. Skip it.
        if brand_data.get('series'):
            print(f"--- Skipping Brand: {brand_name} (already has series data) ---")
            continue

        # If we are here, this brand needs to be scraped.
        print(f"\n--- Processing MISSING Brand: {brand_name} ---")

        series = _scrape_options_from_page(brand_data['url'], 'li.cl3')
        all_car_data[brand_name]['series'] = series
        print(f"Found {len(series)} series for {brand_name}.")

        for series_name, series_data in series.items():
            print(f"  -> Processing Series: {series_name}")
            models = _scrape_options_from_page(series_data['url'], 'li.cl4')
            all_car_data[brand_name]['series'][series_name]['models'] = models
            print(f"  Found {len(models)} models for {series_name}.")

            for model_name, model_data in models.items():
                print(f"    >> Processing Model: {model_name}")
                packets = _scrape_options_from_page(model_data['url'], 'li.cl5')
                all_car_data[brand_name]['series'][series_name]['models'][model_name]['packets'] = list(sorted(packets.keys()))
                print(f"    Found {len(packets)} packets for {model_name}.")

        # Save progress after each brand is fully completed
        print(f"--- Finished Brand: {brand_name}. Saving progress... ---")
        with open(json_file_path, 'w', encoding='utf-8') as f:
            json.dump(all_car_data, f, ensure_ascii=False, indent=4)

    print("\nScraping run finished. Saving final data...")
    if all_car_data:
        with open(json_file_path, 'w', encoding='utf-8') as f:
            json.dump(all_car_data, f, ensure_ascii=False, indent=4)
        print(f"Successfully saved data to '{json_file_path}'!")


if __name__ == "__main__":
    main()
