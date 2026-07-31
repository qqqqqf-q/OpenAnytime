# OpenAnytime Web

基于 Vite、React、Tailwind CSS v4 和 shadcn/ui Base Nova 的本地 CGM 仪表盘。

```bash
pnpm install
pnpm dev
pnpm typecheck
pnpm lint
pnpm build
```

开发服务器将 `/api` 代理到 `OPENANYTIME_API_PROXY_TARGET`，默认值为 `http://127.0.0.1:8520`。生产构建输出到 `dist/`，由仓库根目录的只读 `server.py` 托管。
