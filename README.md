# Lanzou Cloud API

A FastAPI service that parses Lanzou Cloud share links to extract direct download URLs.
Supports single files (with/without password), folder listings, and proxy downloads.

## Base URL

```
http://localhost:8000
```

## Endpoints

### `GET /` -- Resolve Share Link

Parse a Lanzou share link and return the direct download URL, redirect to it, or stream the file.

#### Parameters

| Name   | Type   | Required | Default | Description                                                                 |
|--------|--------|----------|---------|-----------------------------------------------------------------------------|
| `url`  | string | Yes      | --      | Lanzou share link (e.g. `https://www.lanzouf.com/i9S0o3immkfg`)            |
| `pwd`  | string | No       | `""`    | Share password (required for password-protected links)                      |
| `type` | string | No       | `""`    | Response mode: `""` for JSON, `"down"` for redirect, `"file"` for download |
| `n`    | string | No       | `""`    | Custom filename suffix (applied to the download URL)                       |
| `pg`   | int    | No       | `1`     | Page number for folder listings (must be >= 1)                             |

#### Response Modes

**1. JSON (default, `type` omitted or empty)**

Returns parsed file info with the direct download URL.

```
GET /?url=https://www.lanzouf.com/i9S0o3immkfg
```

```json
{
  "code": 200,
  "msg": "Parse successful",
  "name": "Example.rar",
  "filesize": "52.6 M",
  "downUrl": "https://..."
}
```

**2. Redirect (`type=down`)**

Returns a `302` redirect to the direct download URL. Useful for embedding in download buttons.

```
GET /?url=https://www.lanzouf.com/i9S0o3immkfg&type=down
```

Response: `302 Found` with `Location` header pointing to the download URL.

**3. Stream Download (`type=file`)**

Proxies the file download through the server with a `Content-Disposition` header. The filename
is determined by the `n` parameter if provided, otherwise the original filename from the share page.

```
GET /?url=https://www.lanzouf.com/i9S0o3immkfg&type=file
GET /?url=https://www.lanzouf.com/i9S0o3immkfg&type=file&n=custom_name
```

Response: Binary stream with headers:

```
Content-Disposition: attachment; filename="Example.rar"
Content-Type: application/octet-stream
```

**4. Folder Listing (automatic when URL path starts with `b`)**

Folder links are detected automatically. Returns a paginated list of files in the folder.

```
GET /?url=https://www.lanzouf.com/b0raxqelc&pwd=1234&pg=1
```

```json
{
  "code": 200,
  "msg": "Parse successful",
  "name": "Folder Name",
  "fileCount": 10,
  "files": [
    {
      "name": "Example.rar",
      "size": "52.6 M",
      "time": "2024-01-15",
      "icon": "rar",
      "url": "https://www.lanzouf.com/i9S0o3immkfg"
    }
  ]
}
```

Each file entry's `url` can be passed back to this API to get the direct download URL.

#### Password-Protected Links

If a link requires a password and `pwd` is not provided, the API returns:

```json
{
  "code": 400,
  "msg": "Please provide the share password"
}
```

Supply the password via the `pwd` parameter:

```
GET /?url=https://www.lanzouf.com/i9S0o3immkfg&pwd=abcd
```

### `GET /docs` -- Swagger UI

Auto-generated interactive API documentation (provided by FastAPI).

### `GET /redoc` -- ReDoc

Auto-generated API documentation in ReDoc format (provided by FastAPI).

### `GET /openapi.json` -- OpenAPI Schema

Raw OpenAPI 3.x JSON schema for the API.

## Error Responses

All errors return JSON with the following structure:

```json
{
  "code": 400,
  "msg": "Error description"
}
```

| HTTP Status | Message                              | Cause                                            |
|-------------|--------------------------------------|--------------------------------------------------|
| 400         | Please provide a URL                 | `url` parameter is missing or empty              |
| 400         | Invalid Lanzou link                  | URL format is not a valid Lanzou share link       |
| 400         | File sharing has been cancelled      | The shared file has been removed by the uploader |
| 400         | Please provide the share password    | Link is password-protected but `pwd` is empty    |
| 400         | Incorrect password                   | Folder password is wrong                         |
| 400         | Failed to parse page parameters      | Could not extract required data from share page  |
| 400         | Failed to parse download info        | Download info request returned invalid data      |
| 400         | Failed to find iframe link           | Share page structure is unexpected               |
| 400         | Failed to parse iframe parameters    | Iframe page structure is unexpected              |
| 400         | Failed to parse folder parameters    | Folder page structure is unexpected              |

## Usage Examples

### cURL

```bash
# Get download URL as JSON
curl "http://localhost:8000/?url=https://www.lanzouf.com/i9S0o3immkfg"

# Direct redirect download
curl -L "http://localhost:8000/?url=https://www.lanzouf.com/i9S0o3immkfg&type=down" -o file.rar

# Stream download through server
curl "http://localhost:8000/?url=https://www.lanzouf.com/i9S0o3immkfg&type=file" -o file.rar

# Password-protected link
curl "http://localhost:8000/?url=https://www.lanzouf.com/i9S0o3immkfg&pwd=abcd"

# Folder listing
curl "http://localhost:8000/?url=https://www.lanzouf.com/b0raxqelc&pwd=1234&pg=1"
```

### Python

```python
import requests

# Get download URL
resp = requests.get("http://localhost:8000/", params={"url": "https://www.lanzouf.com/i9S0o3immkfg"})
data = resp.json()
print(data["downUrl"])

# Download file through stream proxy
resp = requests.get(
    "http://localhost:8000/",
    params={"url": "https://www.lanzouf.com/i9S0o3immkfg", "type": "file"},
    stream=True,
)
with open("file.rar", "wb") as f:
    for chunk in resp.iter_content(chunk_size=8192):
        f.write(chunk)
```

### JavaScript (Fetch)

```javascript
// Get download URL
const resp = await fetch("http://localhost:8000/?url=https://www.lanzouf.com/i9S0o3immkfg");
const data = await resp.json();
console.log(data.downUrl);

// Trigger browser download via redirect
window.open("http://localhost:8000/?url=https://www.lanzouf.com/i9S0o3immkfg&type=down");
```

## Running the Server

### Install Dependencies

```bash
pip install -r requirements.txt
```

### Start

```bash
# Default: 0.0.0.0:8000 with auto-reload
python main.py

# Custom host and port
python main.py --host=127.0.0.1 --port=3000

# Production (no reload)
python main.py --no-reload

# Or via uvicorn directly
uvicorn main:app --host 0.0.0.0 --port 8000
```

## Notes

- The API normalizes all Lanzou domain variants (`lanzou.com`, `lanzoui.com`, `lanzoux.com`, etc.) to `lanzouf.com`.
- Folder links are detected by the URL path starting with `b` (e.g. `/b0raxqelc`).
- The `n` parameter appends a custom suffix to the filename in the download URL. It does not rename the file arbitrarily.
- The `pid=` parameter is stripped from final download URLs to prevent server IP leakage.
- A 2-second delay is applied during download URL resolution to satisfy the CDN's anti-bot timing requirement.
- CORS is enabled for all origins (`Access-Control-Allow-Origin: *`).
