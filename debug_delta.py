
import requests
import json

def get_delta_products():
    try:
        print("Fetching products...")
        response = requests.get("https://api.india.delta.exchange/v2/products", timeout=10)
        print(f"Status: {response.status_code}")
        if response.status_code == 200:
            return response.json().get('result', [])
        return []
    except Exception as e:
        print(f"Error: {e}")
        return []

products = get_delta_products()
print(f"Total products: {len(products)}")

if products:
    # Print first 2 products to understand structure
    print("\n--- SAMPLE PRODUCT STRUCTURE ---")
    print(json.dumps(products[0], indent=2))
    
    # Check BTC options specifically
    print("\n--- CHECKING BTC OPTIONS ---")
    options = [p for p in products if p.get('contract_type') in ['call_options', 'put_options'] and p.get('underlying_asset_symbol') == 'BTC']
    print(f"Total BTC Options found: {len(options)}")
    
    if options:
        print("\n--- SAMPLE BTC OPTION ---")
        print(json.dumps(options[0], indent=2))
        
        # Check active status logic
        active_options = [o for o in options if o.get('is_active')]
        print(f"Active BTC Options: {len(active_options)}")
    else:
        print("No BTC options found with current filter.")
        
        # Debug why
        check_symbol = [p for p in products if 'BTC' in p.get('symbol', '')]
        print(f"Products with BTC in symbol: {len(check_symbol)}")
        if check_symbol:
            print("First BTC symbol product:", check_symbol[0]['symbol'], check_symbol[0].get('contract_type'), check_symbol[0].get('underlying_asset_symbol'))
