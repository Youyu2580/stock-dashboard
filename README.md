# 红利候选池 · 网页版部署说明

## 文件结构

```
index.html                      页面（手机友好）
data.json                       数据（由 Actions 自动更新）
update.py                       抓数脚本
dividends.json                  分红缓存（每月手动更新一次）
.github/workflows/update.yml    定时任务（工作日 15:35 北京时间）
```

## 部署步骤

1. 在 GitHub 新建仓库，名字建议 `stock-dashboard`，选 **Public**
2. 进入仓库，点「Add file → Upload files」，把本文件夹里的**所有内容**拖进去（包括 `.github` 文件夹）
3. 提交
4. 仓库 → Settings → Pages → Build and deployment：
   - Source 选 **Deploy from a branch**
   - Branch 选 **main**，目录选 **/ (root)**
   - 保存
5. 等 1–2 分钟，访问 `https://Yuyu2580.github.io/stock-dashboard/`
6. 仓库 → Actions → 选中 `update-data` → 点 **Run workflow** 手动跑一次，生成 `data.json`
7. 之后每个工作日 15:35（北京时间）自动更新

## 注意

- 第一次部署时 `data.json` 可能还不存在，先手动跑一次 Actions
- GitHub Actions 免费额度：个人仓库每月 2000 分钟，这个任务每天不到 1 分钟，够用
- 分红数据（`dividends.json`）每月核对一次公告后手动替换
- 如果页面空白，检查 Actions 是否跑成功、Pages 是否部署完成
