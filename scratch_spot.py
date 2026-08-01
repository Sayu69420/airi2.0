import requests
import json
import re

def test_full_fallback(url):
    state = {'container': {'queue': []}, 'songlist': {'s_list': [], 'd_list': [], 'u_list': []}}
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
    url_clean = url.split('?')[0]
    
    if 'spotify.link' in url:
        r_head = requests.head(url, headers=headers, allow_redirects=True)
        url_clean = r_head.url.split('?')[0]
    elif 'spotify:' in url:
        parts = url.split(':')
        if len(parts) >= 3:
            url_clean = f"https://open.spotify.com/{parts[1]}/{parts[2]}"

    embed_url = url_clean if '/embed/' in url_clean else url_clean.replace('open.spotify.com/', 'open.spotify.com/embed/')
    resp = requests.get(embed_url, headers=headers, timeout=10)

    if resp.status_code == 200:
        json_match = re.search(r'<script[^>]*>({"\s*props\s*":.+?})</script>', resp.text, re.DOTALL)
        if json_match:
            data = json.loads(json_match.group(1))
            entity = data.get('props', {}).get('pageProps', {}).get('state', {}).get('data', {}).get('entity', {})
            entity_type = entity.get('type')

            if entity_type in ['playlist', 'album']:
                track_list = entity.get('trackList', []) or entity.get('tracks', {}).get('items', [])
                for t in track_list:
                    title = t.get('title') or t.get('name')
                    artists = t.get('artists') or t.get('subtitle') or t.get('artist')
                    artist_name = ""
                    if isinstance(artists, list) and artists:
                        artist_name = artists[0].get('name', '') if isinstance(artists[0], dict) else str(artists[0])
                    elif isinstance(artists, str):
                        artist_name = artists

                    if title:
                        query_str = f"{artist_name} {title}".strip()
                        display_str = f"{artist_name} - {title}".strip() if artist_name else title
                        state['container']['queue'].append(query_str)
                        state['songlist']['s_list'].append(display_str[:50] + '...' if len(display_str) > 50 else display_str)
                        state['songlist']['d_list'].append('N/A')
                        state['songlist']['u_list'].append(query_str)
            elif entity_type == 'track':
                title = entity.get('title') or entity.get('name')
                artists = entity.get('artists') or entity.get('subtitle')
                artist_name = ""
                if isinstance(artists, list) and artists:
                    artist_name = artists[0].get('name', '') if isinstance(artists[0], dict) else str(artists[0])
                elif isinstance(artists, str):
                    artist_name = artists

                if title:
                    query_str = f"{artist_name} {title}".strip()
                    display_str = f"{artist_name} - {title}".strip() if artist_name else title
                    state['container']['queue'].append(query_str)
                    state['songlist']['s_list'].append(display_str[:50] + '...' if len(display_str) > 50 else display_str)
                    state['songlist']['d_list'].append('N/A')
                    state['songlist']['u_list'].append(query_str)
    
    print(f"URL: {url} -> Queued {len(state['container']['queue'])} tracks:")
    for song in state['songlist']['s_list'][:3]:
        print("  -", song)
    print("-" * 50)

test_full_fallback("https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT?si=123")
test_full_fallback("https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M?si=123")
test_full_fallback("https://open.spotify.com/album/4aawyAB9vmqN3uQ7FjRGTy")
