import json
import sys
import time
import threading
from urllib.parse import urlencode, quote

try:
    from base.spider import Spider as BaseSpider
except ImportError:
    class BaseSpider:
        def init(self, extend=""):
            pass
        def getName(self):
            return ""
        def isVideoFormat(self, url):
            return False
        def manualVideoCheck(self):
            return False
        def destroy(self):
            pass
        def localProxy(self, params):
            return None

try:
    import requests
    from requests.adapters import HTTPAdapter
    from requests.packages.urllib3.util.retry import Retry
    requests.packages.urllib3.disable_warnings()
    _HAS_REQUESTS = True
except Exception:
    requests = None
    HTTPAdapter = None
    Retry = None
    _HAS_REQUESTS = False

HOST   = "http://23.225.14.70:520"
API    = HOST + "/api.php/provide/vod/"         
UA     = "Mozilla/5.0 (Linux; Android 11; TV) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.77 Safari/537.36"
VIDEO_HEADER = {"User-Agent": UA, "Referer": HOST + "/"}

DOU_REFERER = "https://movie.douban.com/"        

CLASSES = [
    {"type_id": "1",  "type_name": "电影"},
    {"type_id": "2",  "type_name": "剧集"},
    {"type_id": "3",  "type_name": "动漫"},
    {"type_id": "4",  "type_name": "综艺"},
    {"type_id": "5",  "type_name": "儿童"},
    {"type_id": "20", "type_name": "纪录"},
    {"type_id": "21", "type_name": "漫剧"},
    {"type_id": "22", "type_name": "短剧"},
]

FILTER_YEAR = "2026,2025,2024,2023,2022,2021,2020,2019,2018,2017,2016,2015,2014,2013,2012,2011,2010,2009,2008,2007,2006,2005,2004,2003,2002,2001,2000,1999,1998,1997,1996,1995,1994,1993,1992,1991,1990"
FILTER_AREA = "中国大陆,中国香港,中国台湾,日本,韩国,美国,英国,法国,德国,意大利,西班牙,泰国,印度,俄罗斯,澳大利亚,加拿大,其他"
FILTER_LANG = "国语,粤语,英语,日语,韩语,法语,德语,西班牙语,泰语,俄语,其他"

CACHE_TTL = {           
    "videolist": 60,
    "detail":    600,
    "search":    60,
}

PLATFORM_HOSTS = ("youku.com", "iqiyi.com", "qiyi.com", "mgtv.com",
                  "bilibili.com", "sohu.com", "tudou.com", "le.com",
                  "qq.com")

class Spider(BaseSpider):
    def __init__(self):
        super().__init__()
        self.siteKey   = "macms_apple"
        self._cache    = {}        # key -> (expire_ts, json_str)
        self._lock     = threading.Lock()
        self._http     = None
        self._init_session()

    def getName(self):
        return "苹果源"

    def init(self, extend=""):
        pass

    def destroy(self):
        try:
            if self._http:
                self._http.close()
        except Exception:
            pass

    def isVideoFormat(self, url):
        if not url:
            return False
        u = url.lower()
        return (".m3u8" in u) or (".mp4" in u) or ("/mpegurl" in u) or ("application/vnd.apple.mpegurl" in u)

    def manualVideoCheck(self):
        return False

    def _init_session(self):
        if not _HAS_REQUESTS:
            return
        s = requests.Session()
        s.headers.update({"User-Agent": UA, "Accept": "application/json, text/plain, */*"})
        if HTTPAdapter and Retry:
            r = Retry(total=2, backoff_factor=0.3, status_forcelist=[500, 502, 503, 504])
            s.mount("http://", HTTPAdapter(max_retries=r, pool_connections=4, pool_maxsize=8))
            s.mount("https://", HTTPAdapter(max_retries=r))
        self._http = s

    def _request(self, params, ttl=0):
        key = "|".join("%s=%s" % (k, params[k]) for k in sorted(params))
        if ttl > 0:
            with self._lock:
                hit = self._cache.get(key)
                if hit and hit[0] > time.time():
                    return hit[1]
        url = API + "?" + urlencode(params)
        text = None
        if self._http is not None:
            try:
                resp = self._http.get(url, timeout=(8, 20), verify=False)
                resp.encoding = "utf-8"
                text = resp.text
            except Exception as e:
                sys.stderr.write("[applesrc] request error: %s\n" % e)
                return None
        else:
            try:
                import urllib.request
                req = urllib.request.Request(url, headers={"User-Agent": UA})
                with urllib.request.urlopen(req, timeout=20) as resp:
                    text = resp.read().decode("utf-8", "replace")
            except Exception as e:
                sys.stderr.write("[applesrc] stdlib error: %s\n" % e)
                return None
        if not text or not text.strip():
            return None
        if not text.lstrip().startswith("{"):
            return None
        if ttl > 0:
            with self._lock:
                self._cache[key] = (time.time() + ttl, text)
                if len(self._cache) > 200:
                    now = time.time()
                    self._cache = {k: v for k, v in self._cache.items() if v[0] > now}
        return text

    def _json(self, params, ttl=0):
        text = self._request(params, ttl)
        if not text:
            return {}
        try:
            obj = json.loads(text)
            return obj if isinstance(obj, dict) else {}
        except Exception:
            return {}

    @staticmethod
    def _fix_pic(pic):
        if not pic:
            return ""
        pic = pic.strip()
        if not pic:
            return ""
        if "@Referer=" in pic:          
            return pic
        if "doubanio.com" in pic:       
            pic = pic + "@Referer=" + DOU_REFERER
        return pic

    def _line_kind(self, first_ep_addr):
        a = (first_ep_addr or "").strip().lower()
        if not a:
            return 2
        if a.startswith(("http://", "https://")):
            tail = a[a.find("//") + 2:]
            host = tail.split("/", 1)[0].split(":", 1)[0]
            if any(h in host for h in PLATFORM_HOSTS):
                return 1
            return 0
        return 2

    def _sort_play_lines(self, play_from, play_url):
        from urllib.parse import unquote as _unq
        fs = (play_from or "线路1").split("$$$")
        us = (play_url or "").split("$$$")
        n = max(len(fs), len(us))
        fs = (fs + [""] * n)[:n]
        us = (us + [""] * n)[:n]
        lines = []
        for f, u in zip(fs, us):
            first_addr = ""
            if u:
                seg = u.split("#", 1)[0]
                if "$" in seg:
                    first_addr = seg.split("$", 1)[1]
            lines.append((self._line_kind(first_addr), f, u))
        lines.sort(key=lambda x: x[0])
        return "$$$".join(x[1] for x in lines), "$$$".join(x[2] for x in lines)

    def _map_video(self, it):
        if not isinstance(it, dict):
            return None
        out = {
            "vod_id":      str(it.get("vod_id", "") or ""),
            "vod_name":    it.get("vod_name", "") or "",
            "vod_pic":     self._fix_pic(it.get("vod_pic") or ""),
            "vod_remarks": it.get("vod_remarks", "") or "",
        }
        for k_src, k_dst in (("vod_year", "vod_year"),
                             ("vod_area", "vod_area"),
                             ("vod_lang", "vod_lang"),
                             ("vod_actor", "vod_actor"),
                             ("vod_director", "vod_director"),
                             ("vod_score", "vod_score"),
                             ("vod_time", "vod_time"),
                             ("type_name", "type_name"),
                             ("vod_class", "vod_class"),
                             ("vod_class_name", "vod_class_name"),
                             ("vod_content", "vod_content")):
            v = it.get(k_src)
            if v not in (None, ""):
                out[k_dst] = v
        if "vod_id" not in out or not out["vod_id"]:
            return None
        if not out["vod_name"]:
            return None
        return out

    def _build_filters(self):
        def items(csv):
            return [{"n": v, "v": v} for v in csv.split(",") if v]
        fs = [
            {"key": "year", "name": "年份", "init": "", "value": [{"n": "全部", "v": ""}] + items(FILTER_YEAR)},
            {"key": "area", "name": "地区", "init": "", "value": [{"n": "全部", "v": ""}] + items(FILTER_AREA)},
            {"key": "lang", "name": "语言", "init": "", "value": [{"n": "全部", "v": ""}] + items(FILTER_LANG)},
        ]
        return {c["type_id"]: [dict(f) for f in fs] for c in CLASSES}

    def homeContent(self, filter):
        try:
            classes = [dict(c) for c in CLASSES]
            items = []
            for tid in ("1", "2", "3"):
                try:
                    data = self._json({"ac": "videolist", "t": tid, "pg": "1"}, CACHE_TTL["videolist"])
                    items.extend((data.get("list") or [])[:20])
                except Exception:
                    continue
            videos = [self._map_video(it) for it in items]
            videos = [v for v in videos if v]
            return {"class": classes, "list": videos,
                    "filters": self._build_filters() if filter else {}}
        except Exception as e:
            sys.stderr.write("[applesrc] homeContent: %s\n" % e)
            return {"class": [dict(c) for c in CLASSES], "list": [],
                    "filters": self._build_filters() if filter else {}}

    def homeVideoContent(self):
        return self.homeContent(False)

    def categoryContent(self, tid, pg, filter, extend):
        try:
            tid  = str(tid or "")
            pg   = int(pg or 1)
            params = {"ac": "videolist", "t": tid, "pg": str(pg)}
            ext = extend or {}
            for k in ("year", "area", "lang"):
                v = ext.get(k)
                if isinstance(v, (list, tuple)):
                    v = v[0] if v else ""
                if v:
                    params[k] = str(v)
            data = self._json(params, CACHE_TTL["videolist"])
            arr = data.get("list") or []
            videos = [self._map_video(it) for it in arr]
            videos = [v for v in videos if v]
            pagecount = int(data.get("pagecount") or 1)
            limit     = int(data.get("limit") or 100)
            total     = int(data.get("total") or 0)
            return {
                "list":      videos,
                "page":      int(data.get("page") or pg),
                "pagecount": pagecount,
                "limit":     limit if limit > 0 else 100,
                "total":     total,
            }
        except Exception as e:
            sys.stderr.write("[applesrc] categoryContent: %s\n" % e)
            return {"list": [], "page": int(pg or 1), "pagecount": 1, "limit": 100, "total": 0}

    def detailContent(self, ids):
        try:
            if not ids:
                return {"list": []}
            vid = str(ids[0])
            data = self._json({"ac": "detail", "ids": vid}, CACHE_TTL["detail"])
            arr = data.get("list") or []
            if not arr:
                return {"list": []}
            it = arr[0]
            vod = self._map_video(it) or {}
            vod["vod_id"] = vid
            new_from, new_url = self._sort_play_lines(it.get("vod_play_from") or "",
                                                       it.get("vod_play_url") or "")
            vod["vod_play_from"] = new_from
            vod["vod_play_url"]  = new_url
            vod["vod_content"]   = it.get("vod_content") or ""
            return {"list": [vod]}
        except Exception as e:
            sys.stderr.write("[applesrc] detailContent: %s\n" % e)
            return {"list": []}

    def playerContent(self, flag, id, vipFlags=None):
        try:
            url = str(id or "")
            header = json.dumps(VIDEO_HEADER, ensure_ascii=False)
            if url.startswith(("http://", "https://")) and self._line_kind(url) == 0:
                return {"parse": 0, "url": url, "header": header}
            return {"parse": 1, "url": url, "header": header}
        except Exception:
            return {"parse": 1, "url": str(id or ""), "header": ""}

    def searchContent(self, key, quick, pg="1"):
        try:
            pg = int(pg or 1)
            data = self._json({"ac": "videolist", "wd": str(key), "pg": str(pg)}, CACHE_TTL["search"])
            arr = data.get("list") or []
            videos = [self._map_video(it) for it in arr]
            videos = [v for v in videos if v]
            return {
                "list":      videos,
                "page":      int(data.get("page") or pg),
                "pagecount": int(data.get("pagecount") or 1),
                "limit":     int(data.get("limit") or 100),
                "total":     int(data.get("total") or 0),
            }
        except Exception as e:
            sys.stderr.write("[applesrc] searchContent: %s\n" % e)
            return {"list": [], "page": int(pg or 1), "pagecount": 1}