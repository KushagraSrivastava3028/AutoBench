# Next.js UI. Multi-stage: dependencies, build, then a minimal runtime.
FROM node:22-alpine AS deps
WORKDIR /ui
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

FROM node:22-alpine AS build
WORKDIR /ui
COPY --from=deps /ui/node_modules ./node_modules
COPY . .
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build

FROM node:22-alpine AS runtime
WORKDIR /ui
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1
COPY --from=build /ui/.next ./.next
COPY --from=build /ui/node_modules ./node_modules
COPY --from=build /ui/package.json ./package.json
COPY --from=build /ui/next.config.ts ./next.config.ts
USER node
EXPOSE 3000
CMD ["npx", "next", "start", "-p", "3000"]
