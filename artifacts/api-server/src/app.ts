import express, { type Express, type Request, type Response } from "express";
import cors from "cors";
import pinoHttp from "pino-http";
import { fileURLToPath } from "url";
import { dirname, join } from "path";
import router from "./routes";
import { logger } from "./lib/logger";

const __filename = fileURLToPath(import.meta.url);
const __dirname_here = dirname(__filename);
const frontendPath = join(__dirname_here, "../../../meeting-assistant/frontend");

const app: Express = express();

app.use(
  pinoHttp({
    logger,
    serializers: {
      req(req) {
        return {
          id: req.id,
          method: req.method,
          url: req.url?.split("?")[0],
        };
      },
      res(res) {
        return {
          statusCode: res.statusCode,
        };
      },
    },
  }),
);
app.use(cors());
app.use(express.json());
app.use(express.urlencoded({ extended: true }));

app.use("/api", router);

// ── Mock endpoints for meeting-assistant frontend ─────────────────────────
// Mounted under /api to match Replit proxy routing (previewPath = /api)

const mockSettingsData = { sensitivity: "balanced", audio_chunk_seconds: 5, audio_sample_rate: 16000 };

app.get("/api/settings", (_req: Request, res: Response) => res.json(mockSettingsData));

app.post("/api/settings/sensitivity", (req: Request, res: Response) => {
  const { sensitivity } = req.body as { sensitivity?: string };
  res.json({ sensitivity: sensitivity ?? "balanced" });
});

app.post("/api/settings/audio_chunk_seconds", (req: Request, res: Response) => {
  const { audio_chunk_seconds } = req.body as { audio_chunk_seconds?: number };
  res.json({ audio_chunk_seconds: audio_chunk_seconds ?? 5 });
});

app.post("/api/settings/audio_sample_rate", (req: Request, res: Response) => {
  const { audio_sample_rate } = req.body as { audio_sample_rate?: number };
  res.json({ audio_sample_rate: audio_sample_rate ?? 16000 });
});

app.get("/api/history", (_req: Request, res: Response) => res.json([]));

app.post("/api/summarise", (_req: Request, res: Response) => {
  res.json({
    summary:
      "TechCorp ($450K) advancing to Proposal stage — legal addendum due by the 20th.\n" +
      "StartupXYZ CRM integration ($120K) contingent on August API delivery — sprint starting Monday.\n" +
      "MegaCorp enterprise renewal ($850K) in Negotiation — SLA amendment with legal, targeting EOM signature.",
  });
});

app.post("/api/upload", (_req: Request, res: Response) => {
  res.json({ ok: true, message: "Demo mode: upload acknowledged" });
});

// ── Serve meeting-assistant frontend under /api prefix ────────────────────

app.use("/api", express.static(frontendPath));

app.use("/api", (_req: Request, res: Response) => {
  res.sendFile(join(frontendPath, "index.html"));
});

export default app;
