// Empty shim for Node.js modules not needed in browser
export function readFileSync() { throw new Error("readFileSync not available in browser"); }
export function deflateSync() { throw new Error("deflateSync not available in browser"); }
export function fileURLToPath(url) { return url; }
