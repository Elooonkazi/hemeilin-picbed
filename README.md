# hemeilin-picbed · 随机图片 API + 图床

参考 [oyz8/random-pic-api](https://github.com/oyz8/random-pic-api) 的结构自主搭建，
并新增了一套**图片爬虫**：图库不再靠手工上传，而是由爬虫自动抓取、处理、入库，
GitHub Actions 定时运行，图库持续增长。部署在 Cloudflare Pages，兼容 EdgeOne Pages。

## 架构

```
爬虫 (Python, GitHub Actions 每周/手动)
  Picsum + Bing 壁纸 ──► 转 WebP ──► 横/竖 × 深/浅 分类 ──► SHA256+感知哈希去重
        │                                                     │
        └──────────────► 提交到 ri/{hd,hl,vd,vl}/<n>.webp ◄───┘
                                  │  ri/count.json 索引
Cloudflare Pages（Git 集成，提交自动部署）
  /            图库首页 + API 文档
  /pic         随机图（302 跳转）   /random 同义别名
  /ri/...      图片静态文件（CDN 直出）
```

## API

| 请求 | 说明 |
| --- | --- |
| `GET /pic` | 随机一张，按 User-Agent 自动选横/竖，302 跳转 |
| `GET /pic?img=h` / `?img=v` / `?img=a` | 指定横图 / 竖图 / 任意 |
| `GET /pic?json=h` / `?json=v` / `?json=a` | JSON 返回 `{theme, folder, file, url}` |
| `GET /ri/count.json` | 图库计数索引 |

防盗链（可选）：在 Pages 环境变量设置 `ALLOWED_DOMAINS=example.com,foo.com`
后仅白名单域名可引用；不设则完全公开。`ALLOW_EMPTY_REQUEST` 控制是否允许无 Referer/Origin 的直连（默认允许）。

## 本地运行爬虫

```bash
pip install -r crawler/requirements.txt
python crawler/crawl.py --picsum-h 24 --picsum-v 24 --bing 8
```

参数：`--seed-prefix` 换一组 Picsum 种子即可抓一批新图；重复图片会被哈希去重跳过。
编号乱了时跑 `python .github/scripts/renumber.py` 重新连续编号并重建索引。

## 目录

- `functions/pic.js` — Pages Function 随机图 API（另有 `functions/random.js` 别名路由）
- `index.html` — 首页/图库
- `ri/` — 图片库与 `count.json`、`hash_registry.json`
- `crawler/` — 爬虫源码
- `.github/workflows/crawl.yml` — 定时爬取并自动提交
- `.github/workflows/renumber.yml` — 手动重编号
