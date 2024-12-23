import requests
from dotenv import load_dotenv
import os

# Load environment variables
load_dotenv()

TENOR_API_KEY = os.getenv("TENOR_API_KEY")
CRYPTO_WALLET_ADDRESS = os.getenv("CRYPTO_WALLET_ADDRESS")


def fetch_memes(search_terms: str):
    """
    Fetch a list of GIFs or images from Tenor based on search keywords.

    Args:
      search_terms (str): Keywords to search for relevant memes or GIFs.

    Returns:
      list[dict]: A list of dictionaries containing GIF descriptions and URLs.
    """
    base_url = "https://g.tenor.com/v1/search"
    params = {
        "api_key": TENOR_API_KEY,
        "q": search_terms,
        "limit": 5,
    }
    response = requests.get(base_url, params=params)
    if response.status_code == 200:
        return [
            {
                "description": item.get("content_description", "No description"),
                "gifUrl": item.get("media", [{}])[0]
                .get("gif", {})
                .get("url", "No GIF URL available"),
            }
            for item in response.json().get("results", [])
        ]
    return []


def verify_payment(receiving_wallet: str, sender_wallet: str, expected_amount: float = 0.001):
    """
    Verify that a specific amount of SOL has been received from a given sender wallet.

    Args:
        receiving_wallet (str): The Solana wallet address that should receive the payment.
        sender_wallet (str): The Solana wallet address that sent the payment.
        expected_amount (float): The amount to verify in SOL (default is 0.001 SOL).

    Returns:
        str: The transaction signature if the payment is verified, else None.
    """
    SOLANA_RPC_URL = "https://api.devnet.solana.com"  # Use Devnet URL for testing
    headers = {"Content-Type": "application/json"}

    # Fetch recent transaction signatures for the sender wallet
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getSignaturesForAddress",
        "params": [sender_wallet, {"limit": 20}]
    }
    response = requests.post(SOLANA_RPC_URL, headers=headers, json=payload)
    
    if response.status_code != 200:
        print("Error fetching transaction signatures:", response.text)
        return None

    transactions = response.json().get("result", [])
    for txn in transactions:
        # Fetch detailed transaction info
        txn_payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTransaction",
            "params": [txn["signature"], "jsonParsed"]
        }
        txn_response = requests.post(SOLANA_RPC_URL, headers=headers, json=txn_payload)
        
        if txn_response.status_code != 200:
            print(f"Error fetching transaction details for {txn['signature']}: {txn_response.text}")
            continue

        txn_data = txn_response.json().get("result", {})
        if not txn_data:
            continue

        instructions = txn_data.get("transaction", {}).get("message", {}).get("instructions", [])
        for instruction in instructions:
            # Check if the instruction involves a transfer to the receiving wallet
            parsed = instruction.get("parsed", {})
            if parsed and parsed.get("info", {}).get("destination") == receiving_wallet:
                source = parsed.get("info", {}).get("source")
                lamports = int(parsed.get("info", {}).get("lamports", 0))
                amount_received = lamports / 1e9  # Convert lamports to SOL
                # print(f"Amount received: {amount_received}")
                # Verify the source and amount
                if source == sender_wallet and amount_received == expected_amount:
                    return txn["signature"]  # Return the transaction signature if matched

    return None
 
