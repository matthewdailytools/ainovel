import asyncio
import hashlib
from html.parser import HTMLParser
from io import BytesIO
import ipaddress
import json
from pathlib import PurePosixPath
import posixpath
import re
import socket
from time import time
from urllib.parse import quote, unquote, urljoin, urlsplit
from zipfile import BadZipFile, ZipFile

from defusedxml import ElementTree as ET
from defusedxml.common import DefusedXmlException
import httpx

from .db import DomainError, dumps, uid
from .domain import utf16_slice

MAX_FILE = 20 * 1024 * 1024
ALLOWED_HOSTS = {"zh.wikisource.org", "wikisource.org", "ws-export.wmcloud.org"}


class PlainHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.hidden = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.hidden += 1
        if tag in ("p", "div", "br", "h1", "h2", "h3", "li") and not self.hidden:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self.hidden:
            self.hidden -= 1

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)

    def text(self):
        return re.sub(r"\n[ \t]*\n(?:[ \t]*\n)+", "\n\n", "".join(self.parts)).strip()


def parse_epub(raw):
    units, errors = [], []
    try:
        with ZipFile(BytesIO(raw)) as archive:
            files = archive.infolist()
            if len(files) > 3000 or sum(item.file_size for item in files) > 64 * 1024 * 1024:
                raise DomainError("archive_limit", "EPUB 解压规模超过保护上限。")
            names = set()
            for item in files:
                path = PurePosixPath(item.filename)
                if (
                    path.is_absolute()
                    or ".." in path.parts
                    or "\\" in item.filename
                    or item.filename in names
                ):
                    raise DomainError("archive_path", "EPUB 包含不安全或重复路径。")
                names.add(item.filename)
            container = ET.fromstring(archive.read("META-INF/container.xml"))
            rootfile = next(node for node in container.iter() if node.tag.endswith("rootfile")).attrib[
                "full-path"
            ]
            if rootfile not in names:
                raise DomainError("epub", "EPUB 缺少有效包文档。")
            package = ET.fromstring(archive.read(rootfile))
            manifest = {
                item.attrib["id"]: item.attrib["href"]
                for item in package.iter()
                if item.tag.endswith("}item")
            }
            spine = [item.attrib["idref"] for item in package.iter() if item.tag.endswith("}itemref")]
            if not spine:
                raise DomainError("epub", "EPUB 没有可识别的正文顺序。")
            for index, ref in enumerate(spine):
                try:
                    href = unquote(manifest[ref].split("#")[0])
                    filename = posixpath.normpath(posixpath.join(posixpath.dirname(rootfile), href))
                    if filename.startswith("../") or filename not in names:
                        raise ValueError("missing spine resource")
                    reader = PlainHTML()
                    reader.feed(archive.read(filename).decode("utf-8-sig"))
                    text = reader.text()
                    if not text or len(text) > 250_000:
                        raise ValueError("empty or oversized unit")
                    units.append((index + 1, filename, text))
                except KeyError, ValueError, UnicodeDecodeError:
                    errors.append(f"第 {index + 1} 个正文单元未成功解析")
            return units, errors, len(spine)
    except (BadZipFile, KeyError, StopIteration, ValueError, ET.ParseError, DefusedXmlException) as error:
        raise DomainError("epub", "EPUB 结构损坏或含不支持的加密／编码。原件已保留。") from error


async def public_url(url):
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in ALLOWED_HOSTS
        or parsed.port not in (None, 443)
        or parsed.username
    ):
        raise DomainError("source_url", "只允许配置的维基文库官方来源。")
    addresses = await asyncio.to_thread(socket.getaddrinfo, parsed.hostname, 443)
    # TUN proxies commonly return RFC 2544 fake-IP addresses. The host is still one
    # of three fixed official DNS names, each redirect is rechecked and TLS name
    # validation remains enabled. Never allow arbitrary IP URLs or private LANs.
    tun_ranges = (ipaddress.ip_network("198.18.0.0/15"), ipaddress.ip_network("2001:2::/48"))
    if not addresses or any(
        not (
            ipaddress.ip_address(item[4][0]).is_global
            or any(ipaddress.ip_address(item[4][0]) in network for network in tun_ranges)
        )
        for item in addresses
    ):
        raise DomainError("source_address", "来源地址不是允许的公网地址。")


class Library:
    def __init__(self, database, data_dir):
        self.db, self.directory = database, data_dir / "sources"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.lock = asyncio.Semaphore(1)
        with self.db.connect(True) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS library_jobs(id TEXT PRIMARY KEY,title TEXT NOT NULL,status TEXT NOT NULL,
                book_id TEXT,error TEXT NOT NULL DEFAULT '',created REAL NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS book_progress(book_id TEXT PRIMARY KEY REFERENCES books(id),
                unit_id TEXT NOT NULL,offset INTEGER NOT NULL,revision INTEGER NOT NULL)""")
            if "format" not in {row[1] for row in db.execute("PRAGMA table_info(library_jobs)")}:
                db.execute("ALTER TABLE library_jobs ADD COLUMN format TEXT NOT NULL DEFAULT 'epub'")

    def import_file(
        self, filename, raw, source="本地导入", encoding="utf-8-sig", job_id=None, source_scope=None
    ):
        if len(raw) > MAX_FILE:
            raise DomainError("file_limit", "资料文件超过 20 MiB，未入库。")
        suffix = filename.rsplit(".", 1)[-1].lower()
        if suffix not in ("txt", "md", "epub"):
            raise DomainError("format", "当前支持 TXT、Markdown 和无 DRM EPUB；PDF/OCR 尚未实现。")
        if encoding not in ("utf-8-sig", "gb18030"):
            raise DomainError("encoding", "请选择 UTF-8 或 GB18030。")
        book_id = uid()
        managed = self.directory / (book_id + "." + suffix)
        with managed.open("xb") as file:
            file.write(raw)
        try:
            if suffix == "epub":
                units, errors, expected = parse_epub(raw)
            else:
                try:
                    text = raw.decode(encoding).replace("\r\n", "\n").replace("\r", "\n")
                except UnicodeDecodeError as error:
                    raise DomainError(
                        "encoding", "无法按所选编码解析；原件保留，可选择另一编码重新导入。"
                    ) from error
                # Keep bounded, immutable reading units without inventing chapter boundaries.
                units = [
                    (index // 12_000 + 1, f"正文单元 {index // 12_000 + 1}", text[index : index + 12_200])
                    for index in range(0, len(text), 12_000)
                ]
                if not units:
                    raise DomainError("empty", "文件没有可阅读正文。")
                errors, expected = [], len(units)
        except DomainError as error:
            units, errors, expected = [], [error.message], 0
        coverage = {
            "parsed": len(units),
            "expected": expected,
            "errors": errors,
            "reader": "纯文本；不执行脚本或加载远端资源",
        }
        if source_scope:
            coverage["source_scope"] = source_scope
        status = "ready" if units and not errors else "partial" if units else "parse_failed"
        with self.db.connect(True) as db:
            if job_id:
                job = db.execute("SELECT status FROM library_jobs WHERE id=?", (job_id,)).fetchone()
                if not job or job[0] == "cancelled":
                    raise DomainError("cancelled", "下载已取消，文件未发布到书库。", 409)
            db.execute(
                "INSERT INTO books VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    book_id,
                    filename,
                    source,
                    hashlib.sha256(raw).hexdigest(),
                    managed.name,
                    suffix,
                    status,
                    dumps(coverage),
                    time(),
                ),
            )
            for position, title, text in units:
                unit_id = uid()
                db.execute(
                    "INSERT INTO source_units VALUES(?,?,?,?,?)", (unit_id, book_id, position, title, text)
                )
                if self.db.fts:
                    db.execute("INSERT INTO source_fts(unit_id,content) VALUES(?,?)", (unit_id, text))
            if job_id:
                db.execute("UPDATE library_jobs SET status=?,book_id=? WHERE id=?", (status, book_id, job_id))
        return self.db.one("SELECT * FROM books WHERE id=?", (book_id,))

    def search(self, query, book_id=None, novel_id=None, offset=0):
        if not query or len(query) > 200 or offset < 0 or offset > 100_000:
            raise DomainError("query", "请输入 1—200 字符的检索词。")
        where, args = [], []
        if book_id:
            where.append("u.book_id=?")
            args.append(book_id)
        if novel_id:
            where.append(
                "u.book_id IN (SELECT u2.book_id FROM excerpts e JOIN source_units u2 ON u2.id=e.unit_id WHERE e.novel_id=?)"
            )
            args.append(novel_id)
        if self.db.fts and len(query) >= 3:
            where.append("u.id IN (SELECT unit_id FROM source_fts WHERE source_fts MATCH ?)")
            args.append('"' + query.replace('"', '""') + '"')
        where.append("instr(u.content,?)>0")
        args.append(query)
        rows = self.db.all(
            "SELECT u.*,b.title AS book_title,b.file_hash FROM source_units u JOIN books b ON b.id=u.book_id WHERE "
            + " AND ".join(where)
            + " ORDER BY b.created DESC,u.position,u.id LIMIT 51 OFFSET ?",
            (*args, offset),
        )
        hits = []
        for row in rows[:50]:
            start = row["content"].find(query)
            row["snippet"] = row["content"][max(0, start - 60) : start + len(query) + 120]
            row["start"] = len(row["content"][:start].encode("utf-16-le")) // 2
            row["end"] = row["start"] + len(query.encode("utf-16-le")) // 2
            row.pop("content")
            hits.append(row)
        coverage = self.db.all(
            "SELECT id,title,status,coverage FROM books" + (" WHERE id=?" if book_id else ""),
            (book_id,) if book_id else (),
        )
        return {
            "hits": hits,
            "query": query,
            "offset": offset,
            "next_offset": offset + 50 if len(rows) > 50 else None,
            "coverage": coverage,
            "matching": "原文连续子串；至少三字时使用可用的 trigram 索引，短词精确扫描。",
        }

    def excerpt(self, unit_id, start, end, quote_text, note, novel_id=None):
        unit = self.db.one("SELECT * FROM source_units WHERE id=?", (unit_id,))
        if utf16_slice(unit["content"], start, end)[1] != quote_text or not quote_text:
            raise DomainError("locator", "摘录文字或定位与原文不一致。")
        if len(note) > 10_000:
            raise DomainError("note_limit", "笔记过长。")
        excerpt_id = uid()
        with self.db.connect(True) as db:
            db.execute(
                "INSERT INTO excerpts VALUES(?,?,?,?,?,?,?,?)",
                (excerpt_id, unit_id, start, end, quote_text, note, novel_id, time()),
            )
        return self.db.one("SELECT * FROM excerpts WHERE id=?", (excerpt_id,))

    async def fetch(self, url, cancelled=lambda: False):
        async with httpx.AsyncClient(
            timeout=90,
            follow_redirects=False,
            headers={"User-Agent": "ainovel/0.1 (personal research reader)"},
        ) as client:
            for _ in range(5):
                await public_url(url)
                async with client.stream("GET", url) as response:
                    if response.status_code in (301, 302, 303, 307, 308):
                        url = urljoin(url, response.headers.get("location", ""))
                        continue
                    if response.status_code != 200:
                        raise DomainError(
                            "source_unavailable",
                            f"官方来源返回 HTTP {response.status_code}，尚未取得文件。",
                            502,
                        )
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        if cancelled():
                            raise DomainError("cancelled", "已取消资料获取。", 409)
                        body.extend(chunk)
                        if len(body) > MAX_FILE:
                            raise DomainError("file_limit", "来源文件超过 20 MiB，已停止下载。")
                    return bytes(body)
        raise DomainError("redirect", "来源重定向次数过多。")

    async def find_books(self, query):
        if not query or len(query) > 200:
            raise DomainError("query", "请输入书名或主题。")
        url = (
            "https://zh.wikisource.org/w/api.php?action=query&list=search&format=json&srlimit=10&srsearch="
            + quote(query)
        )
        try:
            result = json.loads(await self.fetch(url))
        except (ValueError, httpx.HTTPError) as error:
            raise DomainError(
                "source_unavailable", "维基文库暂不可用；可保留关键词或本地导入。", 502
            ) from error
        return [
            {
                "title": row["title"],
                "provider": "wikisource",
                "url": "https://zh.wikisource.org/wiki/" + quote(row["title"]),
                "coverage": "书目命中，尚未下载或全文检索",
                "rights": "以来源页面许可说明为准",
            }
            for row in result.get("query", {}).get("search", [])
        ]

    async def acquire(self, job_id):
        async with self.lock:
            job = self.db.one("SELECT * FROM library_jobs WHERE id=?", (job_id,))

            def cancelled():
                return (
                    self.db.one("SELECT status FROM library_jobs WHERE id=?", (job_id,))["status"]
                    == "cancelled"
                )

            try:
                if cancelled():
                    return
                with self.db.connect(True) as db:
                    db.execute("UPDATE library_jobs SET status='downloading' WHERE id=?", (job_id,))
                source = "https://zh.wikisource.org/wiki/" + quote(job["title"])
                if job["format"] == "page_text":
                    data = json.loads(
                        await self.fetch(
                            "https://zh.wikisource.org/w/api.php?action=parse&format=json&formatversion=2&prop=text&page="
                            + quote(job["title"]),
                            cancelled,
                        )
                    )
                    html = data.get("parse", {}).get("text")
                    if not isinstance(html, str):
                        raise DomainError("source_unavailable", "官方来源没有返回该页面正文。")
                    reader = PlainHTML()
                    reader.feed(html)
                    raw, suffix = reader.text().encode("utf-8"), ".txt"
                    scope = "维基文库当前单页面正文；未跟随子页面、不代表多页面书籍全文。"
                else:
                    raw = await self.fetch(
                        "https://ws-export.wmcloud.org/?lang=zh&format=epub-3&page=" + quote(job["title"]),
                        cancelled,
                    )
                    suffix, scope = ".epub", "官方 EPUB 导出包的 spine 正文；解析范围见 parsed/expected。"
                if cancelled():
                    return
                self.import_file(job["title"] + suffix, raw, source, job_id=job_id, source_scope=scope)
            except asyncio.CancelledError:
                with self.db.connect(True) as db:
                    db.execute(
                        "UPDATE library_jobs SET status='failed',error='本机服务停止，获取尚未完成；可重新获取。' WHERE id=? AND status IN ('queued','downloading')",
                        (job_id,),
                    )
                raise
            except (DomainError, httpx.HTTPError, OSError, ValueError) as error:
                with self.db.connect(True) as db:
                    db.execute(
                        "UPDATE library_jobs SET status=CASE WHEN status='cancelled' THEN status ELSE 'failed' END,error=? WHERE id=?",
                        (
                            error.message
                            if isinstance(error, DomainError)
                            else "下载或保存失败，可重新获取；原有书库保持可用。",
                            job_id,
                        ),
                    )
