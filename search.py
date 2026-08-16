import asyncio
import base64
import json
import os
import re
from urllib.parse import parse_qs, urljoin, urlsplit

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright


def decode_go_url(raw_url: str) -> str:
    if not raw_url:
        return ""

    raw_url = raw_url.strip()
    if raw_url.startswith("http://") or raw_url.startswith("https://"):
        return raw_url

    if "?" not in raw_url:
        return raw_url

    query = raw_url.split("?", 1)[1]
    params = parse_qs(query)
    t = params.get("t", [""])[0]
    if t:
        try:
            pad = "=" * (-len(t) % 4)
            return base64.b64decode(t + pad).decode("utf-8", "ignore")
        except Exception:
            pass

    return raw_url


def parse_meta_value(meta_text: str, key: str) -> str:
    if not meta_text:
        return ""

    text = meta_text.replace("：", ":")
    text = normalize_text(text)

    patterns = {
        "author": [
            r"作者\s*:\s*(.+)",
            r"作者\s+(.+)",
        ],
        "category": [
            r"分类\s*:\s*(.+)",
            r"类型\s*:\s*(.+)",
            r"类别\s*:\s*(.+)",
        ],
    }

    for pattern in patterns.get(key, []):
        m = re.search(pattern, text, flags=re.I)
        if m:
            return normalize_text(m.group(1))

    return ""


def normalize_text(text):
    if text is None:
        return ""
    return re.sub(r"\s+", " ", str(text)).strip()


def parse_meta_items(card):
    author = ""
    category = ""

    for item in card.select("span.book-card__meta-item"):
        label_node = item.select_one("span.book-card__meta-label")
        label = normalize_text(label_node.get_text(" ", strip=True)) if label_node else ""
        full_text = normalize_text(item.get_text(" ", strip=True))

        if not label:
            if "作者" in full_text:
                author = full_text.replace("作者", "", 1).replace("：", "").replace(":", "").strip()
            elif "分类" in full_text or "类型" in full_text or "类别" in full_text:
                category = full_text.replace("分类", "", 1).replace("类型", "", 1).replace("类别", "", 1).replace("：", "").replace(":", "").strip()
            continue

        value = full_text.replace(label, "", 1).replace("：", "").replace(":", "").strip()

        if "作者" in label:
            author = value
        elif "分类" in label or "类型" in label or "类别" in label:
            category = value

    return author, category


def parse_novel_card(card):
    title = ""
    title_node = card.select_one("h3.book-card__title")
    if title_node:
        title = normalize_text(title_node.get_text(" ", strip=True))

    if not title:
        return None

    novel_url = ""
    onclick = card.get("onclick", "")
    if onclick:
        m = re.search(r"window\.open\((?:['\"])([^'\"]+)(?:['\"])", onclick)
        if m:
            novel_url = decode_go_url(m.group(1))

    if not novel_url:
        a_node = card.select_one("a[href]")
        if a_node:
            novel_url = decode_go_url(a_node.get("href", ""))

    novel_author, novel_category = parse_meta_items(card)

    desc = ""
    desc_node = card.select_one("p.book-card__desc")
    if desc_node:
        desc = normalize_text(desc_node.get_text(" ", strip=True))

    tags = []
    for tag in card.select("span.book-tag"):
        tag_text = normalize_text(tag.get_text(" ", strip=True))
        if tag_text:
            tags.append(tag_text)

    novel_tags = tags[0] if tags else novel_category

    status = ""
    status_node = card.select_one("span.book-tag--status")
    if status_node:
        status = normalize_text(status_node.get_text(" ", strip=True))

    novel_update = ""
    update_node = card.select_one("span.book-card__update")
    if update_node:
        novel_update = normalize_text(update_node.get_text(" ", strip=True)).replace("更新：", "", 1).strip()

    return {
        "novel_url": novel_url,
        "novel_title": title,
        "novel_author": novel_author,
        "novel_category": novel_category,
        "novel_desc": desc,
        "novel_tags": novel_tags,
        "novel_status": status,
        "novel_update": novel_update,
    }


def extract_novels_from_html(html: str):
    soup = BeautifulSoup(html, "html.parser")
    cards = soup.select("div.book-card")
    if not cards:
        return []

    result = []
    seen = set()

    for card in cards:
        item = parse_novel_card(card)
        if not item:
            continue

        key = (item["novel_title"], item["novel_url"])
        if key in seen:
            continue
        seen.add(key)
        result.append(item)

    return result


def extract_page_url_from_html(html: str, target_page: int) -> str:
    soup = BeautifulSoup(html, "html.parser")

    for a in soup.select("a[href]"):
        href = a.get("href", "")
        if not href:
            continue

        if "action=find" not in href and "page=" not in href:
            continue

        full_url = urljoin("https://www.rrssk.com/", href)
        query = urlsplit(full_url).query
        params = parse_qs(query)
        page_value = params.get("page", [""])[0]

        if page_value == str(target_page):
            return full_url

    return ""


async def search_and_get_first_page(page, keyword: str):
    await page.goto("https://www.rrssk.com/boluomao.html", wait_until="domcontentloaded", timeout=30000)
    search_input = page.locator("input[name='keyword']")
    await search_input.wait_for(state="visible", timeout=15000)
    await search_input.fill(keyword)
    await search_input.press("Enter")
    await page.wait_for_load_state("domcontentloaded", timeout=30000)
    await page.wait_for_timeout(1500)


async def goto_target_page(page, target_page: int):
    html = await page.content()
    target_url = extract_page_url_from_html(html, target_page)
    if target_url:
        await page.goto(target_url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(1500)
        return True
    return False


async def perform_search():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.5845.97 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
            ignore_https_errors=True,
        )
        page = await context.new_page()

        try:
            keyword = "精灵"
            out_dir = os.path.join(os.getcwd(), "搜索结果")
            os.makedirs(out_dir, exist_ok=True)

            await search_and_get_first_page(page, keyword)

            page_num = 1
            while page_num <= 50:
                print(f"正在抓取第 {page_num} 页...")
                html = await page.content()
                novels = extract_novels_from_html(html)

                print(f"第 {page_num} 页抓到 {len(novels)} 本小说")

                if not novels:
                    break

                # 每一页保存一个 JSON，里面是该页所有小说
                file_path = os.path.join(out_dir, f"{page_num}.json")
                with open(file_path, "w", encoding="utf-8") as f:
                    json.dump(novels, f, ensure_ascii=False, indent=2)
                    f.write("\n")

                print(f"已保存第 {page_num} 页到: {file_path}")

                if page_num >= 50:
                    break

                next_ok = await goto_target_page(page, page_num + 1)
                if not next_ok:
                    break

                page_num += 1

        except Exception as e:
            print(f"发生错误: {str(e)}")
        finally:
            await browser.close()


asyncio.run(perform_search())
