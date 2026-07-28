import { cp, mkdir, readFile, readdir, rm, writeFile } from "node:fs/promises";
import { extname, join, relative, resolve, sep } from "node:path";

const projectRoot = resolve(import.meta.dirname, "..");
const sourceDir = join(projectRoot, "docs");
const outputDir = join(projectRoot, "dist");
const serverDir = join(outputDir, "server");
const staticDir = join(outputDir, "static");

const contentTypes = {
  ".css": "text/css; charset=utf-8",
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".jpeg": "image/jpeg",
  ".jpg": "image/jpeg",
  ".mp4": "video/mp4",
  ".png": "image/png",
  ".svg": "image/svg+xml",
  ".webp": "image/webp",
};

async function collectFiles(directory) {
  const entries = await readdir(directory, { withFileTypes: true });
  const files = [];

  for (const entry of entries) {
    const absolute = join(directory, entry.name);
    if (entry.isDirectory()) {
      files.push(...(await collectFiles(absolute)));
    } else if (entry.isFile()) {
      files.push(absolute);
    }
  }

  return files;
}

await rm(outputDir, { recursive: true, force: true });
await mkdir(serverDir, { recursive: true });
await cp(sourceDir, staticDir, { recursive: true });

const assetEntries = await Promise.all(
  (await collectFiles(sourceDir)).map(async (absolute) => {
    const key = relative(sourceDir, absolute).split(sep).join("/");
    const body = await readFile(absolute);
    const type = contentTypes[extname(absolute).toLowerCase()] || "application/octet-stream";
    return [key, { body: body.toString("base64"), type }];
  })
);

const worker = `const encodedAssets = ${JSON.stringify(Object.fromEntries(assetEntries))};
const decodedAssets = new Map();

function decodeBase64(value) {
  const binary = atob(value);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }
  return bytes;
}

function resolveAsset(pathname) {
  let clean;
  try {
    clean = decodeURIComponent(pathname);
  } catch {
    return null;
  }
  while (clean.startsWith("/")) clean = clean.slice(1);
  if (!clean) return "index.html";
  if (encodedAssets[clean]) return clean;
  if (clean.endsWith("/") && encodedAssets[clean + "index.html"]) return clean + "index.html";
  return null;
}

export default {
  async fetch(request) {
    if (request.method !== "GET" && request.method !== "HEAD") {
      return new Response("Method Not Allowed", {
        status: 405,
        headers: { Allow: "GET, HEAD" },
      });
    }

    const key = resolveAsset(new URL(request.url).pathname);
    if (!key) {
      return new Response("Not Found", { status: 404 });
    }

    const asset = encodedAssets[key];
    if (!decodedAssets.has(key)) {
      decodedAssets.set(key, decodeBase64(asset.body));
    }

    const headers = new Headers({
      "Content-Type": asset.type,
      "Referrer-Policy": "strict-origin-when-cross-origin",
      "X-Content-Type-Options": "nosniff",
      "X-Frame-Options": "SAMEORIGIN",
    });

    if (key.endsWith(".html")) {
      headers.set("Cache-Control", "no-store");
    } else if (key.endsWith(".css") || key.endsWith(".js")) {
      headers.set("Cache-Control", "no-cache");
    } else {
      headers.set("Cache-Control", "public, max-age=86400");
    }

    const body = decodedAssets.get(key);
    headers.set("Content-Length", String(body.byteLength));

    const range = request.headers.get("Range");
    if (asset.type === "video/mp4") {
      headers.set("Accept-Ranges", "bytes");
    }
    if (range && asset.type === "video/mp4") {
      const match = /^bytes=(\\d*)-(\\d*)$/.exec(range);
      if (!match || (!match[1] && !match[2])) {
        return new Response(null, {
          status: 416,
          headers: { "Content-Range": \`bytes */\${body.byteLength}\` },
        });
      }

      const suffixLength = !match[1] && match[2] ? Number(match[2]) : null;
      const start = suffixLength === null
        ? Number(match[1])
        : Math.max(0, body.byteLength - suffixLength);
      const end = suffixLength === null && match[2]
        ? Number(match[2])
        : body.byteLength - 1;
      if (start > end || start >= body.byteLength || end >= body.byteLength) {
        return new Response(null, {
          status: 416,
          headers: { "Content-Range": \`bytes */\${body.byteLength}\` },
        });
      }

      const partial = body.slice(start, end + 1);
      headers.set("Content-Range", \`bytes \${start}-\${end}/\${body.byteLength}\`);
      headers.set("Content-Length", String(partial.byteLength));
      return new Response(request.method === "HEAD" ? null : partial, {
        status: 206,
        headers,
      });
    }

    return new Response(request.method === "HEAD" ? null : body, {
      status: 200,
      headers,
    });
  },
};
`;

await writeFile(join(serverDir, "index.js"), worker, "utf8");
console.log(`Built ${assetEntries.length} assets into ${join(serverDir, "index.js")}`);
