import bencodepy
import hashlib

def decode_value(v):
    if isinstance(v, bytes):
        try:
            return v.decode('utf-8')
        except UnicodeDecodeError:
            return v
    elif isinstance(v, dict):
        return decode_dict(v)
    elif isinstance(v, list):
        return [decode_value(i) for i in v]
    else:
        return v

def decode_dict(d):
    new_dict = {}
    for k, v in d.items():
        key = k.decode('utf-8') if isinstance(k, bytes) else k
        new_dict[key] = decode_value(v)
    return new_dict

def parse_torrent_file(file_path):
    with open(file_path, 'rb') as f:
        raw_data = f.read()

    decoded_data = bencodepy.decode(raw_data)
    final_data = decode_dict(decoded_data)
    announce_url=final_data.get('announce')

    # Fallback to announce-list if announce is missing
    if not announce_url and 'announce-list' in final_data:
        # Use the first tracker in the first tier
        announce_list = final_data['announce-list']
        if isinstance(announce_list, list) and len(announce_list) > 0:
            first_tier = announce_list[0]
            if isinstance(first_tier, list) and len(first_tier) > 0:
                announce_url = first_tier[0]
            elif isinstance(first_tier, str):
                announce_url = first_tier

    print("\n=== Torrent Metadata ===")
    print("Tracker URL:", announce_url)

    info = None
    if 'info' in final_data:
        info_raw = final_data['info']
        if isinstance(info_raw, dict):
            info = decode_dict(info_raw)
            print("Torrent Type:", "Multi-file" if 'files' in info else "Single-file")
            print("File/Folder Name:", info.get('name'))
            if 'length' in info:
                print("File Length (bytes):", info['length'])
            if 'files' in info:
                total_length = sum(f['length'] for f in info['files'] if 'length' in f)
                print("Total Size (multi-file, bytes):", total_length)
                print("Included Files:")
                for f in info['files']:
                    path = "/".join(f['path']) if 'path' in f else 'unknown'
                    print(f"  - {path} ({f.get('length', 'unknown')} bytes)")
            print("Piece Length:", info.get('piece length'))
            pieces = info.get('pieces')
            if isinstance(pieces, bytes):
                num_pieces = len(pieces) // 20
                print("Number of Pieces:", num_pieces)
            else:
                print("Pieces: Not in byte format")
        else:
            print("Torrent file is corrupted or malformed in 'info'")
    else:
        print("'info' section missing – invalid torrent file.")

    # Return info dict for verification
    return info,announce_url

#this function is required to create a tcp handshake later
def getinfo_hash(torrent_path):
    with open(torrent_path, 'rb') as f:
        raw = f.read()
    decoded = bencodepy.decode(raw)#decoded is a python dictionaey of the entire torrent file.
    #we dont know where info section is so we decode the entire file.. access the info section as decode[b'info'] & encode it again in bencode and then convert to sha1 hash
    info_bencoded = bencodepy.encode(decoded[b'info'])
    info_hash = hashlib.sha1(info_bencoded).digest()
    return info_hash