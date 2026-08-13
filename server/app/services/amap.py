"""高德 Web 服务：为「一起去」方案提供真实地点/天气/通勤数据。

- 未配 AMAP_KEY（或任一查询失败）时各函数返回 None/[]，调用方整体回退纯 LLM 方案
- 只读接口，个人开发者免费额度；v3 经典端点，字段全部防御性解析
- 价格类数据高德不提供，方案里一律预估区间 + 跳转链接（见 prompts.PLAN_PROMPT 规则）
- 数字签名（AMAP_SECRET 非空时）：除 sig 外全部参数（含 key）按参数名升序
  key=value 用 & 连接，末尾拼安全密钥私钥后 MD5，作为 sig 参数发出
"""
import hashlib
import logging

import httpx

from ..config import settings

logger = logging.getLogger("us.amap")

_BASE = "https://restapi.amap.com"
_TIMEOUT = 10.0


def _sig(params: dict) -> str:
    """数字签名：参数（含 key、不含 sig）按名升序拼接，末尾加私钥，MD5 小写。"""
    base = "&".join(f"{k}={params[k]}" for k in sorted(params))
    return hashlib.md5((base + settings.AMAP_SECRET).encode("utf-8")).hexdigest()


def _get(path: str, params: dict) -> dict | None:
    """统一 GET：status=1 才返回 info 体，其余（含无 key/超限/网络异常）返回 None。"""
    if not settings.AMAP_KEY:
        return None
    params = {**params, "key": settings.AMAP_KEY}
    if settings.AMAP_SECRET:
        params["sig"] = _sig(params)
    try:
        resp = httpx.get(f"{_BASE}{path}", params=params, timeout=_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        if str(data.get("status")) != "1":
            logger.warning("高德 %s 返回异常：%s", path, data.get("info"))
            return None
        return data
    except Exception as exc:  # noqa: BLE001
        logger.warning("高德 %s 调用失败：%s", path, exc)
        return None


def geocode_city(city: str) -> dict | None:
    """城市名 → {"adcode", "location"(经度,纬度)}；查不到返回 None。"""
    data = _get("/v3/geocode/geo", {"address": city})
    codes = (data or {}).get("geocodes") or []
    if not codes:
        return None
    return {"adcode": codes[0].get("adcode", ""), "location": codes[0].get("location", "")}


def search_poi(keywords: str, city: str = "", types: str = "", limit: int = 5) -> list[dict]:
    """关键字搜 POI：返回 [{name, address, location, rating, cost}]，无结果返回 []。"""
    params: dict = {
        "keywords": keywords,
        "offset": limit,
        "output": "json",
    }
    if city:
        params.update({"city": city, "citylimit": "true"})
    if types:
        params["types"] = types
    data = _get("/v3/place/text", params)
    pois = []
    for p in (data or {}).get("pois") or []:
        biz = p.get("biz_ext") or {}
        pois.append(
            {
                "name": p.get("name", ""),
                "address": p.get("address", "") if isinstance(p.get("address"), str) else "",
                "location": p.get("location", ""),
                "rating": biz.get("rating", "") if isinstance(biz, dict) else "",
                "cost": biz.get("cost", "") if isinstance(biz, dict) else "",
            }
        )
    return pois


def weather_forecast(adcode: str) -> list[dict]:
    """天气预报（未来几天）：[{date, dayweather, daytemp, nighttemp}]；无 adcode 返回 []。"""
    if not adcode:
        return []
    data = _get("/v3/weather/weatherInfo", {"city": adcode, "extensions": "all"})
    forecasts = (data or {}).get("forecasts") or []
    casts = forecasts[0].get("casts") if forecasts else []
    return [
        {
            "date": c.get("date", ""),
            "dayweather": c.get("dayweather", ""),
            "daytemp": c.get("daytemp", ""),
            "nighttemp": c.get("nighttemp", ""),
        }
        for c in (casts or [])
    ]


def driving_leg(origin_xy: str, dest_xy: str) -> dict | None:
    """两点间驾车距离/时长：{"distance_km", "duration_min"}；失败返回 None。"""
    if not origin_xy or not dest_xy:
        return None
    data = _get("/v3/distance", {"origins": origin_xy, "destination": dest_xy, "type": 1})
    results = (data or {}).get("results") or []
    if not results:
        return None
    meters = float(results[0].get("distance", 0) or 0)
    seconds = float(results[0].get("duration", 0) or 0)
    return {"distance_km": round(meters / 1000, 1), "duration_min": round(seconds / 60)}


def gather(city: str, keywords: str) -> dict:
    """汇总方案所需真实数据：景点 POI + 酒店 POI + 天气 + 相邻景点驾车通勤。

    单项失败只缺该项，不整体失败（调用方把空段从 prompt 里略去）。
    """
    geo = geocode_city(city) if city else None
    adcode = (geo or {}).get("adcode", "")
    spots = search_poi(keywords, city) if keywords else []
    hotels = search_poi("酒店", city, types="100000") if city else []  # typecode 100000=住宿服务
    weather = weather_forecast(adcode)
    legs = []
    coords = [s["location"] for s in spots[:3] if s.get("location")]
    for a, b in zip(coords, coords[1:]):
        leg = driving_leg(a, b)
        if leg:
            legs.append(leg)
    return {
        "city": city,
        "spots": spots,
        "hotels": hotels,
        "weather": weather,
        "legs": legs,
    }
