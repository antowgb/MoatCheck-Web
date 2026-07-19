// Empty by default: the frontend is served by the backend, /api calls are
// same-origin. In dev (next dev on :3000), set NEXT_PUBLIC_API_URL=http://localhost:8000.
const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "";

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// The backend runs on a free-tier host that spins down after inactivity and
// takes tens of seconds to wake up: the first request(s) after idle can hit a
// transient 5xx (or fail at the network level) while it's still starting.
// Retrying a couple of times, only for read (GET) requests, smooths over
// that cold start instead of surfacing it as a hard failure the user has to
// manually retry (e.g. by hard-refreshing the page).
const RETRYABLE_STATUS = new Set([500, 502, 503, 504]);
const MAX_ATTEMPTS = 3;
const RETRY_DELAY_MS = 1200;

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const method = (init?.method ?? "GET").toUpperCase();
  const canRetry = method === "GET";

  let lastError: unknown;
  for (let attempt = 1; attempt <= (canRetry ? MAX_ATTEMPTS : 1); attempt++) {
    try {
      const res = await fetch(`${API_URL}/api${path}`, {
        ...init,
        headers: { "Content-Type": "application/json", ...init?.headers },
        cache: "no-store",
      });
      if (!res.ok) {
        if (canRetry && RETRYABLE_STATUS.has(res.status) && attempt < MAX_ATTEMPTS) {
          await sleep(RETRY_DELAY_MS);
          continue;
        }
        const body = await res.text();
        throw new Error(`API ${res.status}: ${body}`);
      }
      return res.json() as Promise<T>;
    } catch (e) {
      lastError = e;
      // A thrown non-ok response (above) is rethrown immediately, not retried
      // again here; this catch is for network-level failures (fetch itself
      // rejecting, e.g. the connection being refused while the host wakes up).
      if (e instanceof Error && e.message.startsWith("API ")) throw e;
      if (canRetry && attempt < MAX_ATTEMPTS) {
        await sleep(RETRY_DELAY_MS);
        continue;
      }
      throw e;
    }
  }
  throw lastError;
}

// `request()` throws Error("API {status}: {raw JSON body}"); pull the
// backend's `detail` message back out so callers can show its exact
// wording, not the wrapped "API 422: {...}" string.
export function backendErrorMessage(e: unknown): string {
  const raw = String(e);
  const jsonStart = raw.indexOf("{");
  if (jsonStart === -1) return raw;
  try {
    const parsed = JSON.parse(raw.slice(jsonStart));
    if (typeof parsed.detail === "string") return parsed.detail;
  } catch {
    // fall through to raw message
  }
  return raw;
}

export interface Stock {
  ticker: string;
  name: string | null;
  sector: string | null;
  industry: string | null;
  currency: string | null;
  status: "active" | "pending_refresh";
  asset_type: "equity" | "etf";
  sector_benchmark_ticker: string | null;
  composite_score: number | null;
  computed_at: string | null;
  updated_at: string;
}

export interface ScreenerRow extends Stock {
  fundamental_score: number | null;
  risk_score: number | null;
  revenue_growth_yoy: number | null;
  market_cap: number | null;
  pe_trailing: number | null;
  debt_to_ebitda: number | null;
}

export interface ScreenerResult {
  rows: ScreenerRow[];
}

export interface ScoreBreakdown {
  fundamental?: { missing: string[]; components: Record<string, { weight: number; normalized: number }> };
  risk?: { missing: string[]; components: Record<string, number> };
  composite?: { missing: string[]; weights: Record<string, number> };
}

export interface StockDetail {
  stock: Stock;
  fundamentals: Record<string, number | string | null> | null;
  score: {
    volatility_annualized: number | null;
    sharpe_ratio: number | null;
    max_drawdown: number | null;
    fundamental_score: number | null;
    risk_score: number | null;
    composite_score: number | null;
    score_breakdown: ScoreBreakdown | null;
    computed_at: string;
  } | null;
  recent_prices: { date: string; close: number; volume: number | null }[];
}

export interface PricePoint {
  date: string;
  close: number;
}

export interface CurvePoint {
  date: string;
  value: number;
}

export interface AddStockResult {
  ticker: string;
  status: string;
  queued: boolean;
  priority: string;
}

export interface BacktestResult {
  error?: string;
  start_date?: string;
  top_n?: number | null;
  exact_tickers?: boolean;
  selected_tickers?: string[];
  scores_at_start?: Record<string, number>;
  tickers_excluded?: { ticker: string; reason: string }[];
  tickers_excluded_count?: number;
  tickers_scorable_count?: number;
  total_universe_count?: number;
  basket_coverage_ratio?: number | null;
  universe_scorable_ratio?: number | null;
  low_sample_warning?: boolean;
  low_sample_warning_message?: string | null;
  note?: string | null;
  basket?: { total_return: number | null; sharpe: number | null };
  benchmark?: { ticker: string; total_return: number | null; sharpe: number | null };
  benchmark_data_unavailable?: boolean;
  per_stock_vs_benchmarks?: {
    ticker: string;
    stock_return: number | null;
    benchmark_ticker: string;
    benchmark_return: number | null;
    vs_benchmark: number | null;
    sector_benchmark_ticker: string | null;
    sector_benchmark_available: boolean;
    sector_return: number | null;
    vs_sector_benchmark: number | null;
  }[];
  basket_curve?: CurvePoint[];
  benchmark_curve?: CurvePoint[];
}

export interface CompareResult {
  start_date: string;
  series: { ticker: string; total_return: number | null; curve: CurvePoint[] }[];
  excluded: { ticker: string; reason: string }[];
}

export interface CorrelationResult {
  tickers: string[];
  matrix: number[][];
}

export interface PortfolioWeightsResult {
  weights: Record<string, number>;
  excluded: { ticker: string; reason: string }[];
}

export interface EfficientFrontierPoint {
  target_return: number;
  volatility: number;
  weights: Record<string, number>;
}

export interface MaxSharpePoint {
  expected_return: number;
  volatility: number;
  sharpe_ratio: number | null;
  weights: Record<string, number>;
}

export interface EfficientFrontierResult {
  tickers: string[];
  excluded: { ticker: string; reason: string }[];
  frontier: EfficientFrontierPoint[];
  skipped_target_returns: number[];
  max_sharpe_point: MaxSharpePoint;
  note: string | null;
}

export interface BenchmarkOption {
  ticker: string;
  name: string | null;
  is_benchmark: boolean;
  sector_name?: string | null;
}

export const api = {
  listStocks: () => request<Stock[]>("/stocks"),
  listBenchmarks: () => request<BenchmarkOption[]>("/stocks/benchmarks"),
  addStock: (ticker: string, adminKey: string, assetType: "equity" | "etf" = "equity") =>
    request<AddStockResult>("/stocks", {
      method: "POST",
      headers: { "X-Admin-Key": adminKey },
      body: JSON.stringify({ ticker, asset_type: assetType }),
    }),
  updateStock: (ticker: string, adminKey: string, body: { sector_benchmark_ticker: string | null }) =>
    request<Stock>(`/stocks/${ticker}`, {
      method: "PATCH",
      headers: { "X-Admin-Key": adminKey },
      body: JSON.stringify(body),
    }),
  stockDetail: (ticker: string) => request<StockDetail>(`/stocks/${ticker}`),
  priceHistory: (ticker: string) => request<PricePoint[]>(`/stocks/${ticker}/history`),
  screener: (params: {
    sector?: string;
    min_score?: number;
    min_growth?: number;
    min_risk_score?: number;
    market_cap_min?: number;
    market_cap_max?: number;
    pe_max?: number;
    debt_to_ebitda_max?: number;
  }) => {
    const q = new URLSearchParams();
    if (params.sector) q.set("sector", params.sector);
    if (params.min_score !== undefined) q.set("min_score", String(params.min_score));
    if (params.min_growth !== undefined) q.set("min_growth", String(params.min_growth));
    if (params.min_risk_score !== undefined) q.set("min_risk_score", String(params.min_risk_score));
    if (params.market_cap_min !== undefined) q.set("market_cap_min", String(params.market_cap_min));
    if (params.market_cap_max !== undefined) q.set("market_cap_max", String(params.market_cap_max));
    if (params.pe_max !== undefined) q.set("pe_max", String(params.pe_max));
    if (params.debt_to_ebitda_max !== undefined) q.set("debt_to_ebitda_max", String(params.debt_to_ebitda_max));
    const qs = q.toString();
    return request<ScreenerResult>(`/screener${qs ? `?${qs}` : ""}`);
  },
  refresh: (tickers: string[]) =>
    request<{ results: { ticker: string; ok: boolean; error?: string }[] }>("/refresh", {
      method: "POST",
      body: JSON.stringify({ tickers }),
    }),
  recompute: () => request<{ results: unknown[] }>("/score/recompute", { method: "POST" }),
  backtest: (body: { start_date: string; top_n?: number; benchmark: string; tickers?: string[] }) =>
    request<BacktestResult>("/backtest", { method: "POST", body: JSON.stringify(body) }),
  compare: (tickers: string[], startDate: string) => {
    const q = new URLSearchParams({ tickers: tickers.join(","), start_date: startDate });
    return request<CompareResult>(`/compare?${q.toString()}`);
  },
  correlation: (tickers: string[]) => {
    const q = new URLSearchParams({ tickers: tickers.join(",") });
    return request<CorrelationResult>(`/correlation?${q.toString()}`);
  },
  portfolioWeights: (tickers: string[]) => {
    const q = new URLSearchParams({ tickers: tickers.join(",") });
    return request<PortfolioWeightsResult>(`/portfolio/weights?${q.toString()}`);
  },
  efficientFrontier: (tickers: string[]) => {
    const q = new URLSearchParams({ tickers: tickers.join(",") });
    return request<EfficientFrontierResult>(`/portfolio/efficient-frontier?${q.toString()}`);
  },
};
