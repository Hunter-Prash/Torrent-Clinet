import requests
import urllib.parse # Keep import if you use it elsewhere, but not for params encoding here
import bencodepy
import os

def getpeerID():
    # Define your client's prefix (usually 7 bytes)
    # For example, '-PC001-' is 7 bytes
    client_prefix = b'-PC001-'

    # Generate the remaining 13 random bytes to make the total length 20
    random_suffix = os.urandom(13)

    # Combine them to get the 20-byte peer_id
    peerId = client_prefix + random_suffix

    # You can add a print statement to verify the length:
    #print(f"Generated peer_id: {peerId!r}, Length: {len(peerId)}")
    return peerId


def get_peers(tracker_url, info_hash, peer_id, total_length, port=6881):
    """
    Makes an announce request to a BitTorrent tracker and returns a list of peers.

    Args:
        tracker_url (str): The URL of the tracker.
        info_hash (bytes): The 20-byte SHA1 hash of the bencoded info dictionary.
                           Must be a bytes object.
        peer_id (bytes): The 20-byte client-generated peer ID.
                         Must be a bytes object.
        total_length (int): The total length of the file(s) in bytes.
        port (int): The port your client is listening on (default 6881).

    Returns:
        list: A list of (ip, port) tuples for discovered peers. Returns an empty
              list if an error occurs or no peers are found.
    """
    params = {
        'info_hash': info_hash, # requests will correctly URL-encode this bytes object
        'peer_id': peer_id,     # requests will correctly URL-encode this bytes object
        'port': port,
        'uploaded': 0,          # Amount uploaded so far
        'downloaded': 0,        # Amount downloaded so far
        'left': total_length,   # Remaining bytes to download
        'compact': 1,           # Request a compact peer list (IP:Port packed into 6 bytes)
        'event': 'started'      # Inform tracker that download has started
    }

    try:
        # Make the GET request to the tracker with a timeout
        response = requests.get(tracker_url, params=params, timeout=10)

        # Raise an HTTPError for bad responses (4xx or 5xx)
        response.raise_for_status()

        # The tracker's response should be bencoded.
        tracker_data = bencodepy.decode(response.content)

        # Check for a 'failure reason' key in the tracker's response, which
        # indicates an error reported by the tracker itself (e.g., invalid info_hash)
        if b'failure reason' in tracker_data:
            failure_reason = tracker_data[b'failure reason'].decode('utf-8')
            print(f"Tracker reported failure: {failure_reason}")
            return []

        # Get the 'peers' key from the tracker response.
        # Trackers can return peers in two formats:
        # 1. Compact: a binary string of 6-byte entries (4 for IP, 2 for port)
        # 2. Non-compact: a list of dictionaries, each with 'ip' and 'port' keys
        peers_data = tracker_data.get(b'peers')

        if not peers_data:
            print("No 'peers' key found in tracker response or peers list is empty.")
            return []

        peer_list = []
        if isinstance(peers_data, list):
            # Non-compact peer list (list of dictionaries)
            for peer_info in peers_data:
                try:
                    ip = peer_info[b'ip'].decode('utf-8')
                    port = peer_info[b'port']
                    peer_list.append((ip, port))
                except (KeyError, UnicodeDecodeError) as e:
                    print(f"Error parsing non-compact peer info: {e} in {peer_info}")
        elif isinstance(peers_data, bytes):
            # Compact peer list (binary string)
            for i in range(0, len(peers_data), 6):
                if i + 6 > len(peers_data):
                    print(f"Warning: Incomplete peer entry at offset {i}. Skipping.")
                    break
                ip = '.'.join(str(b) for b in peers_data[i:i+4])
                port = int.from_bytes(peers_data[i+4:i+6], 'big')
                peer_list.append((ip, port))
        else:
            print(f"Unexpected 'peers' format from tracker: {type(peers_data)}. Full response: {tracker_data}")
            return []

        return peer_list

    except requests.exceptions.HTTPError as e:
        # Handle HTTP errors (e.g., 400 Bad Request, 404 Not Found, 500 Internal Server Error)
        print(f"HTTP Error from tracker {tracker_url}: {e.response.status_code} - {e.response.reason}")
        if e.response and e.response.text:
            # Print the tracker's specific error message if available in the response body
            print(f"Tracker response content: {e.response.text}")
        return []
    except requests.exceptions.ConnectionError as e:
        print(f"Connection Error to tracker {tracker_url}: {e}")
        return []
    except requests.exceptions.Timeout as e:
        print(f"Timeout Error connecting to tracker {tracker_url}: {e}")
        return []
    except requests.exceptions.RequestException as e:
        # Catch any other requests-related exceptions
        print(f"An unexpected Request error occurred with tracker {tracker_url}: {e}")
        return []
    except bencodepy.exceptions.DecodingError as e:
        # This means the tracker returned non-bencoded data (e.g., an HTML page)
        print(f"Decoding Error from tracker {tracker_url}: {e}")
        if response.content: # Check if response.content exists from previous try block
            print(f"Raw tracker response (first 500 bytes): {response.content[:500]!r}")
            # If you want to see the full content if it's not too large:
            # print(f"Full raw tracker response: {response.content!r}")
        return []
    except KeyError as e:
        # This could happen if a crucial key like 'peers' is missing after successful decoding
        print(f"Missing expected key in tracker response: {e}")
        print(f"Full tracker data: {tracker_data}")
        return []
    except Exception as e:
        # Catch any other unforeseen errors
        print(f"An unexpected error occurred: {e}")
        return []

