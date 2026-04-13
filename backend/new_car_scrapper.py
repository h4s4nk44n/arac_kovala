import json
import time
from collections import OrderedDict
from scrapling.fetchers import StealthyFetcher

# --- CONFIGURATION ---
# The root categories to scrape, mapped to their desired output filenames
ROOT_CATEGORIES = {
    "elektrikli-otomobil": "https://www.sahibinden.com/otomobil/elektrikli",
    "elektrikli-arazi-suv-pickup": "https://www.sahibinden.com/arazi-suv-pickup/elektrikli",
}


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
                '#searchResultLeft-category, ul.category-list',
                timeout=15000
            )
        except Exception:
            print("WARNING: Page content did not load after 15 seconds.")

        page.wait_for_timeout(500)

        scroll_pane_selector = '#searchCategoryContainer .jspPane'
        scroll_container_selector = '#searchCategoryContainer'

        # Check if detailed filter list exists
        scroll_pane = page.query_selector(scroll_pane_selector)

        if scroll_pane:
            # Detailed filter list found - check for scrollbar
            scrollbar = page.query_selector(f'{scroll_container_selector} .jspVerticalBar')

            # Scrape initially visible items
            elements = page.query_selector_all(f"{scroll_pane_selector} {item_selector} a")
            for element in elements:
                name = (element.text_content() or '').strip().split('\n')[0]
                href = element.get_attribute('href')
                if name and href and name not in options:
                    options[name] = {'url': href}

            if scrollbar:
                # Scroll to load all items
                print("Scrollbar detected. Using PAGE_DOWN for scrolling.")
                scroll_container = page.query_selector(scroll_container_selector)
                last_option_count = -1
                retries = 3

                while retries > 0:
                    elements = page.query_selector_all(f"{scroll_pane_selector} {item_selector} a")
                    for element in elements:
                        name = (element.text_content() or '').strip().split('\n')[0]
                        href = element.get_attribute('href')
                        if name and href and name not in options:
                            options[name] = {'url': href}

                    if len(options) == last_option_count:
                        retries -= 1
                        print(f"Scroll did not reveal new items. Retries left: {retries}")
                        page.wait_for_timeout(1000)
                    else:
                        retries = 3

                    last_option_count = len(options)

                    if retries == 0:
                        print("No new items found after multiple retries.")
                        break

                    print(f"Scrolling down... Current option count: {len(options)}")
                    if scroll_container:
                        scroll_container.press("PageDown")
                    page.wait_for_timeout(800)
            else:
                print("No scrollbar detected. List is short, scraping complete.")
        else:
            # Simplified category view fallback
            print("Detailed filter list not found. Using simplified category view.")
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
    Main function to scrape each category into its own separate JSON file.
    """
    for category_name, category_url in ROOT_CATEGORIES.items():
        output_filename = f"{category_name.replace('-', '_')}.json"
        print(f"\n\n{'='*60}")
        print(f"STARTING SCRAPE FOR CATEGORY: '{category_name}'")
        print(f"Data will be saved to: '{output_filename}'")
        print(f"{'='*60}\n")

        category_data = OrderedDict()

        # 1. Get all brands for the current category
        print(f"\n--- Scraping Brands from: {category_url} ---")
        brands = _scrape_options_from_page(category_url, 'li.cl2')
        category_data.update(brands)

        # 2. Scrape details for each brand within THIS category
        for brand_name, brand_data in category_data.items():
            print(f"\n--- Processing Brand: {brand_name} ---")
            series = _scrape_options_from_page(brand_data['url'], 'li.cl3')
            brand_data['series'] = series
            print(f"Found {len(series)} series for {brand_name}.")

            for series_name, series_data in series.items():
                print(f"  -> Processing Series: {series_name}")
                models = _scrape_options_from_page(series_data['url'], 'li.cl4')
                series_data['models'] = models
                print(f"    Found {len(models)} models for {series_name}.")

                for model_name, model_data in models.items():
                    print(f"      >> Processing Model: {model_name}")
                    packets = _scrape_options_from_page(model_data['url'], 'li.cl5')
                    model_data['packets'] = list(sorted(packets.keys()))
                    print(f"        Found {len(packets)} packets for {model_name}.")

        # 3. Save the completed data for this category to its file
        print(f"\n--- FINISHED CATEGORY: '{category_name}'. Saving data to '{output_filename}'... ---")
        with open(output_filename, 'w', encoding='utf-8') as f:
            json.dump(category_data, f, ensure_ascii=False, indent=4)
        print("Save complete.")

    print("\nAll scraping runs finished.")


if __name__ == "__main__":
    main()
