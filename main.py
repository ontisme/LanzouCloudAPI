"""Lanzou Cloud share link parser API."""

import asyncio
import json
import random
import re
import sys

import httpx
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse

app = FastAPI(
    title="Lanzou Cloud API",
    description="Parse Lanzou Cloud share links to extract direct download URLs. "
    "Supports single files (with/without password) and folder listings.",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/72.0.3626.121 Safari/537.36"
)

BASE_DOMAIN = "https://www.lanzouf.com"

IP_FIRST_OCTETS = [
    "218", "218", "66", "66", "218", "218", "60", "60",
    "202", "204", "66", "66", "66", "59", "61", "60",
    "222", "221", "66", "59", "60", "60", "66", "218",
    "218", "62", "63", "64", "66", "66", "122", "211",
]


def rand_ip() -> str:
    """Generate a random IP address for request header spoofing."""
    first = random.choice(IP_FIRST_OCTETS)
    return f"{first}.{random.randint(60, 255)}.{random.randint(60, 255)}.{random.randint(60, 255)}"


def acw_sc_v2_simple(arg1: str) -> str:
    """Generate the acw_sc__v2 cookie value via XOR decryption."""
    pos_list = [
        15, 35, 29, 24, 33, 16, 1, 38, 10, 9,
        19, 31, 40, 27, 22, 23, 25, 13, 6, 11,
        39, 18, 20, 8, 14, 21, 32, 26, 2, 30,
        7, 4, 17, 5, 3, 28, 34, 37, 12, 36,
    ]
    mask = "3000176000856006061501533003690027800375"
    output_list = [""] * len(pos_list)

    for j, pos in enumerate(pos_list):
        if pos - 1 < len(arg1):
            output_list[j] = arg1[pos - 1]

    arg2 = "".join(output_list)
    result = ""
    for i in range(0, min(len(arg2), len(mask)), 2):
        xor_value = int(arg2[i:i + 2], 16) ^ int(mask[i:i + 2], 16)
        result += format(xor_value, "02x")

    return result


def _spoofed_headers() -> dict[str, str]:
    """Return random IP spoofing headers with default User-Agent."""
    ip = rand_ip()
    return {
        "X-FORWARDED-FOR": ip,
        "CLIENT-IP": ip,
        "User-Agent": DEFAULT_USER_AGENT,
    }


async def http_get(url: str, user_agent: str = "") -> str:
    """Perform a GET request with random IP headers."""
    headers = _spoofed_headers()
    if user_agent:
        headers["User-Agent"] = user_agent
    async with httpx.AsyncClient(
        follow_redirects=True, verify=False, timeout=10.0
    ) as client:
        resp = await client.get(url, headers=headers)
        return resp.text


async def http_post(
    post_data: dict,
    url: str,
    referer: str = "",
    user_agent: str = "",
    extra_headers: dict[str, str] | None = None,
) -> str:
    """Perform a POST request with random IP headers."""
    headers = _spoofed_headers()
    if user_agent:
        headers["User-Agent"] = user_agent
    if referer:
        headers["Referer"] = referer
    if extra_headers:
        headers.update(extra_headers)
    async with httpx.AsyncClient(
        follow_redirects=False, verify=False, timeout=10.0
    ) as client:
        resp = await client.post(url, data=post_data, headers=headers)
        return resp.text


async def http_head(
    url: str, referer: str, user_agent: str, cookie: str
) -> str:
    """Follow redirects to extract the final download URL."""
    headers = {
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;"
            "q=0.9,image/webp,image/apng,*/*;q=0.8"
        ),
        "Accept-Encoding": "gzip, deflate",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "Pragma": "no-cache",
        "Upgrade-Insecure-Requests": "1",
        "User-Agent": user_agent,
        "Referer": referer,
        "Cookie": cookie,
    }
    async with httpx.AsyncClient(
        follow_redirects=False, verify=False, timeout=10.0
    ) as client:
        resp = await client.get(url, headers=headers)
        return resp.headers.get("location", "")


def _json_error(code: int, msg: str) -> JSONResponse:
    """Return an error JSON response."""
    return JSONResponse(
        content={"code": code, "msg": msg},
        status_code=code,
    )


def _safe_json_loads(text: str) -> dict | list | None:
    """Safely parse JSON, returning None on failure."""
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None


async def _stream_download(
    download_url: str, filename: str
) -> StreamingResponse | JSONResponse:
    """Proxy-stream a file download with a custom Content-Disposition filename.

    Opens a streaming GET request to *download_url* and forwards the bytes
    through a ``StreamingResponse``.  The ``httpx.AsyncClient`` and response
    are created outside an ``async with`` block so their lifetime extends
    across the full streaming duration; they are closed in the generator's
    ``finally`` clause (or immediately on connection failure).
    """
    headers = _spoofed_headers()
    headers["Accept"] = "*/*"

    client = httpx.AsyncClient(verify=False, timeout=60.0, follow_redirects=True)
    try:
        resp = await client.send(
            client.build_request("GET", download_url, headers=headers),
            stream=True,
        )
    except Exception:
        await client.aclose()
        raise

    # Surface upstream HTTP errors instead of silently forwarding them.
    if resp.status_code >= 400:
        await resp.aread()
        await resp.aclose()
        await client.aclose()
        return JSONResponse(
            content={"code": resp.status_code, "msg": "Upstream download failed"},
            status_code=resp.status_code,
        )

    content_type = resp.headers.get("content-type", "application/octet-stream")
    content_length = resp.headers.get("content-length")

    resp_headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
    }
    if content_length:
        resp_headers["Content-Length"] = content_length

    async def forward_chunks():
        try:
            async for chunk in resp.aiter_bytes(chunk_size=65536):
                yield chunk
        finally:
            await resp.aclose()
            await client.aclose()

    return StreamingResponse(
        content=forward_chunks(),
        media_type=content_type,
        headers=resp_headers,
    )


async def _resolve_download_url(dom: str, file_url: str) -> str:
    """Resolve the final direct download URL from the intermediate dom/url.

    Supports both the new verification-page flow (POST to ajax.php)
    and the legacy redirect flow.
    """
    intermediate_url = f"{dom}/file/{file_url}"
    headers = _spoofed_headers()
    headers["User-Agent"] = DEFAULT_USER_AGENT

    async with httpx.AsyncClient(verify=False, timeout=30.0) as session:
        session.cookies.set("down_ip", "1")

        # Step 1: GET the intermediate URL (follow redirects for initial load)
        resp = await session.get(
            intermediate_url, headers=headers, follow_redirects=True
        )
        page = resp.text

        # Step 2: Handle acw_sc__v2 cookie challenge if present
        arg_match = re.search(r"arg1='(.*?)'", page)
        if arg_match:
            decrypted = acw_sc_v2_simple(arg_match.group(1))
            session.cookies.set("acw_sc__v2", decrypted)

            # Check for legacy redirect flow first
            resp = await session.get(
                intermediate_url, headers=headers, follow_redirects=False
            )
            if resp.is_redirect:
                return resp.headers.get("location", "")

            # Re-fetch with follow_redirects to load the verification page
            resp = await session.get(
                intermediate_url, headers=headers, follow_redirects=True
            )
            page = resp.text

        # Step 3: New verification-page flow - extract file/sign params
        # from the down_r() function's AJAX data block
        file_match = re.search(r"'file'\s*:\s*'([^']+)'", page)
        sign_match = re.search(r"'sign'\s*:\s*'([^']+)'", page)

        if file_match and sign_match:
            # The server requires a delay before accepting the POST
            # (matching the 2-second setTimeout in the verification page)
            await asyncio.sleep(2)

            ajax_url = f"{dom}/file/ajax.php"
            post_headers = {
                **headers,
                "X-Requested-With": "XMLHttpRequest",
                "Referer": intermediate_url,
                "Origin": dom,
            }
            resp2 = await session.post(
                ajax_url,
                data={
                    "file": file_match.group(1),
                    "el": 2,
                    "sign": sign_match.group(1),
                },
                headers=post_headers,
                follow_redirects=False,
            )
            final_json = _safe_json_loads(resp2.text)
            if final_json and final_json.get("zt") == 1:
                download_url = final_json.get("url", "")
                if download_url:
                    return download_url

        return ""


def _extract_ajax_var(key: str, page_html: str) -> str | None:
    """Extract an AJAX variable value from a Lanzou page.

    The page embeds variables like 't' and 'k' indirectly:
        var someVar = 'actualValue';
        ...  't': someVar  ...
    This helper resolves the indirection and returns the value,
    or None if extraction fails.
    """
    ref_match = re.search(rf"'{key}'\s*:\s*(\w+)", page_html)
    if not ref_match:
        return None
    var_name = ref_match.group(1)
    val_match = re.search(
        rf"var\s+{re.escape(var_name)}\s*=\s*'([^']*)'", page_html
    )
    if not val_match:
        return None
    return val_match.group(1)


async def _resolve_folder(
    folder_url: str, pwd: str, pg: int
) -> JSONResponse:
    """Resolve a Lanzou folder share link and return the file listing."""
    page_html = await http_get(folder_url)

    # Extract folder name from <title>
    title_match = re.search(r"<title>(.*?)</title>", page_html)
    folder_name = title_match.group(1) if title_match else ""

    # Extract fid from the AJAX URL: /filemoreajax.php?file={fid}
    fid_match = re.search(r"filemoreajax\.php\?file=(\d+)", page_html)
    if not fid_match:
        return _json_error(400, "Failed to parse folder parameters")
    fid = fid_match.group(1)

    # Extract uid from the AJAX data block: 'uid':'...'
    uid_match = re.search(r"'uid'\s*:\s*'(\d+)'", page_html)
    if not uid_match:
        return _json_error(400, "Failed to parse folder uid")
    uid = uid_match.group(1)

    # Extract the indirectly-referenced 't' and 'k' values
    t_val = _extract_ajax_var("t", page_html)
    if t_val is None:
        return _json_error(400, "Failed to extract t value")

    k_val = _extract_ajax_var("k", page_html)
    if k_val is None:
        return _json_error(400, "Failed to extract k value")

    # POST to filemoreajax.php to retrieve the file listing
    post_data = {
        "lx": "2",
        "fid": fid,
        "uid": uid,
        "pg": str(pg),
        "rep": "0",
        "t": t_val,
        "k": k_val,
        "up": "1",
        "ls": "1",
        "pwd": pwd,
    }
    post_url = f"{BASE_DOMAIN}/filemoreajax.php?file={fid}"
    ajax_resp = await http_post(
        post_data,
        post_url,
        referer=folder_url,
        extra_headers={
            "X-Requested-With": "XMLHttpRequest",
            "Origin": BASE_DOMAIN,
        },
    )
    ajax_json = _safe_json_loads(ajax_resp)
    if ajax_json is None:
        return _json_error(400, "Failed to parse folder response")

    zt = ajax_json.get("zt")
    if zt == 3:
        return _json_error(400, ajax_json.get("info", "Incorrect password"))
    if zt != 1:
        return _json_error(400, ajax_json.get("info", "Unknown error"))

    # Build file list from the response, skipping placeholder entries
    files = []
    for item in ajax_json.get("text", []):
        if item.get("id") == "-1":
            continue
        file_id = item.get("id", "")
        files.append({
            "name": item.get("name_all", ""),
            "size": item.get("size", ""),
            "time": item.get("time", ""),
            "icon": item.get("icon", ""),
            "url": f"{BASE_DOMAIN}/{file_id}" if file_id else "",
        })

    return JSONResponse(content={
        "code": 200,
        "msg": "Parse successful",
        "name": folder_name,
        "fileCount": len(files),
        "files": files,
    })




@app.get("/")
async def resolve(
    url: str = Query(default="", description="Lanzou share link"),
    pwd: str = Query(default="", description="Share password"),
    response_type: str = Query(default="", alias="type", description="'down' for redirect, 'file' for direct download"),
    n: str = Query(default="", description="Custom filename suffix"),
    pg: int = Query(default=1, ge=1, description="Page number for folder listings"),
) -> JSONResponse | RedirectResponse | StreamingResponse:
    # Validate URL parameter
    if not url:
        return _json_error(400, "Please provide a URL")

    # Normalize URL to lanzouf.com domain
    parts = url.split(".com/", 1)
    if len(parts) < 2:
        return _json_error(400, "Invalid Lanzou link")
    normalized_url = f"{BASE_DOMAIN}/{parts[1]}"

    # Detect folder links (path starts with 'b', e.g. /b0raxqelc)
    path_segment = parts[1].split("?")[0].split("#")[0]
    if path_segment.startswith("b"):
        return await _resolve_folder(normalized_url, pwd, pg)

    # Fetch the share page
    page_html = await http_get(normalized_url)

    # Check if file sharing has been cancelled (English and Chinese variants)
    cancelled_markers = [
        "File sharing has been cancelled",
        "\u6587\u4ef6\u53d6\u6d88\u5206\u4eab\u4e86",
    ]
    if any(marker in page_html for marker in cancelled_markers) or \
       "file sharing cancelled" in page_html.lower():
        return _json_error(400, "File sharing has been cancelled")

    # Extract file name
    soft_name = ""
    name_patterns = [
        r'style="font-size: 30px;text-align: center;padding: 56px 0px 20px 0px;">(.*?)</div>',
        r'<div class="n_box_3fn".*?>(.*?)</div>',
        r"var filename = '(.*?)';",
        r'div class="b"><span>(.*?)</span></div>',
    ]
    for pattern in name_patterns:
        m = re.search(pattern, page_html, re.DOTALL)
        if m:
            soft_name = m.group(1)
            break

    # Extract file size
    soft_filesize = ""
    size_patterns = [
        r'<div class="n_filesize".*?>\u5927\u5c0f\uff1a(.*?)</div>',
        r'<span class="p7">\u6587\u4ef6\u5927\u5c0f\uff1a</span>(.*?)<br>',
    ]
    for pattern in size_patterns:
        m = re.search(pattern, page_html, re.DOTALL)
        if m:
            soft_filesize = m.group(1)
            break

    # Password-protected link handling
    if "function down_p(){" in page_html:
        if not pwd:
            return _json_error(400, "Please provide the share password")

        sign_matches = re.findall(r"'sign':'(.*?)',", page_html)
        ajaxm_matches = re.findall(r"ajaxm\.php\?file=(\d+)", page_html)

        if len(sign_matches) < 2 or not ajaxm_matches:
            return _json_error(400, "Failed to parse page parameters")

        post_data = {
            "action": "downprocess",
            "sign": sign_matches[1],
            "p": pwd,
            "kd": "1",
        }
        post_url = f"{BASE_DOMAIN}/ajaxm.php?file={ajaxm_matches[0]}"
        ajax_resp = await http_post(post_data, post_url, normalized_url)
        ajax_json = _safe_json_loads(ajax_resp)
        if ajax_json is None:
            return _json_error(400, "Failed to parse download info")

        soft_name = ajax_json.get("inf", soft_name)

    else:
        # Non-password link handling
        link_match = re.search(
            r'<iframe[^>]*name="[\s\S]*?"[\s]+src="/(.*?)"', page_html
        )
        if not link_match:
            return _json_error(400, "Failed to find iframe link")

        iframe_url = f"{BASE_DOMAIN}/{link_match.group(1)}"
        iframe_html = await http_get(iframe_url)

        wp_sign_matches = re.findall(r"wp_sign = '(.*?)'", iframe_html)
        signs_matches = re.findall(r"ajaxdata = '(.*?)'", iframe_html)
        ajaxm_matches = re.findall(r"ajaxm\.php\?file=(\d+)", iframe_html)

        if not wp_sign_matches or not signs_matches or len(ajaxm_matches) < 2:
            return _json_error(400, "Failed to parse iframe parameters")

        post_data = {
            "action": "downprocess",
            "websignkey": signs_matches[0],
            "signs": signs_matches[0],
            "sign": wp_sign_matches[0],
            "websign": "",
            "kd": "1",
            "ves": "1",
        }
        post_url = f"{BASE_DOMAIN}/ajaxm.php?file={ajaxm_matches[1]}"
        ajax_resp = await http_post(post_data, post_url, iframe_url)
        ajax_json = _safe_json_loads(ajax_resp)
        if ajax_json is None:
            return _json_error(400, "Failed to parse download info")

    # Verify download info response
    if ajax_json.get("zt") != 1:
        return _json_error(400, ajax_json.get("inf", "Unknown error"))

    # Resolve the final direct download URL
    fallback_url = f"{ajax_json['dom']}/file/{ajax_json['url']}"
    down_url = await _resolve_download_url(ajax_json["dom"], ajax_json["url"])

    # Fall back to intermediate URL if resolution failed
    if not down_url or "http" not in down_url:
        down_url = fallback_url
    elif n:
        # Apply custom filename suffix if provided
        rename_match = re.search(r"(.*?)\?fn=(.*?)\.", down_url)
        down_url = rename_match.group(0) + n if rename_match else down_url

    # Strip pid= parameter to prevent server IP leakage
    down_url = re.sub(r"pid=(.*?)&", "", down_url)

    # Return redirect, streamed download, or JSON based on type parameter
    if response_type == "down":
        return RedirectResponse(url=down_url)

    if response_type == "file":
        download_name = n or soft_name or "download"
        return await _stream_download(down_url, download_name)

    return JSONResponse(
        content={
            "code": 200,
            "msg": "Parse successful",
            "name": soft_name,
            "filesize": soft_filesize,
            "downUrl": down_url,
        },
    )


if __name__ == "__main__":
    import uvicorn

    host = "0.0.0.0"
    port = 8000
    reload = True

    for arg in sys.argv[1:]:
        if arg.startswith("--host="):
            host = arg.split("=", 1)[1]
        elif arg.startswith("--port="):
            port = int(arg.split("=", 1)[1])
        elif arg == "--no-reload":
            reload = False

    uvicorn.run("main:app", host=host, port=port, reload=reload)
