import Link from "next/link";
import Card from "@/components/Card";

export const metadata = {
  title: "Methodology - MoatCheck",
};

export default function MethodologyPage() {
  return (
    <div className="space-y-8 max-w-3xl">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight text-slate-900 dark:text-slate-50 mb-2">Methodology</h1>
        <p className="text-sm text-slate-600 dark:text-slate-400">
          This page explains, in plain language, how the scores shown on the dashboard, the
          screener, and stock pages are calculated. The content is strictly descriptive:{" "}
          <strong>this is not investment advice</strong>, and this tool doesn&apos;t generate
          buy or sell signals.
        </p>
      </div>

      <nav className="flex flex-wrap gap-3 text-sm">
        <a href="#composite" className="text-emerald-600 dark:text-emerald-400 hover:underline">
          Composite score
        </a>
        <a href="#fundamental" className="text-emerald-600 dark:text-emerald-400 hover:underline">
          Fundamental score
        </a>
        <a href="#risk" className="text-emerald-600 dark:text-emerald-400 hover:underline">
          Risk score
        </a>
        <a href="#correlation" className="text-emerald-600 dark:text-emerald-400 hover:underline">
          Correlation
        </a>
        <a href="#weights" className="text-emerald-600 dark:text-emerald-400 hover:underline">
          Position sizing
        </a>
        <a href="#frontier" className="text-emerald-600 dark:text-emerald-400 hover:underline">
          Efficient frontier
        </a>
        <a href="#backtest" className="text-emerald-600 dark:text-emerald-400 hover:underline">
          Backtest and rebalancing
        </a>
        <a href="#qualitative" className="text-emerald-600 dark:text-emerald-400 hover:underline">
          Qualitative tally
        </a>
        <a href="#limits" className="text-emerald-600 dark:text-emerald-400 hover:underline">
          Known limitations
        </a>
      </nav>

      <Card title="Composite score" className="scroll-mt-4" id="composite">
        <p className="text-sm mb-3">
          The composite score (0 to 100, shown as a badge everywhere in the tool) combines the
          fundamental score and the risk score with the following formula:
        </p>
        <pre className="bg-slate-50 dark:bg-slate-800/60 border border-slate-200 dark:border-slate-800 rounded-lg p-3 text-sm mb-3 overflow-x-auto font-mono">
          composite = 0.6 × fundamental + 0.4 × risk
        </pre>
        <p className="text-sm mb-3">
          If one of the two components is missing (for example, not enough price history yet
          to compute a risk score), the other counts for 100%: the composite
          score is never silently penalized by a missing data point.
        </p>
        <p className="text-sm text-slate-600 dark:text-slate-400">
          <strong>0.6 / 0.4 is an adjustable weighting</strong>, not an absolute financial principle.
          It reflects a choice, giving more weight to a company&apos;s fundamental quality than to its
          stock price&apos;s historical stability, but a different trade-off (e.g. 50/50, or 70/30)
          would be just as defensible. Nothing in financial theory mandates this exact
          ratio.
        </p>
      </Card>

      <Card title="Fundamental score" className="scroll-mt-4" id="fundamental">
        <p className="text-sm mb-4">
          The fundamental score (0 to 100) measures a company&apos;s economic quality from
          5 indicators, each normalized between 0 and 1 then weighted:
        </p>
        <div className="overflow-x-auto mb-4">
          <table className="min-w-full text-sm border-collapse">
            <thead>
              <tr className="text-left text-xs uppercase text-slate-500 dark:text-slate-400 border-b border-slate-200 dark:border-slate-800">
                <th className="py-2 pr-4">Indicator</th>
                <th className="py-2 pr-4">Weight</th>
                <th className="py-2 pr-4">0 (worst) to 1 (best)</th>
                <th className="py-2">Why</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 dark:divide-slate-800 align-top">
              <tr>
                <td className="py-2 pr-4 font-medium">Revenue growth (YoY)</td>
                <td className="py-2 pr-4">25%</td>
                <td className="py-2 pr-4">0% → 0, ≥ 30% → 1</td>
                <td className="py-2">
                  A company whose revenue is stagnating or declining structurally has
                  less long-term room to maneuver than a growing company.
                </td>
              </tr>
              <tr>
                <td className="py-2 pr-4 font-medium">Operating margin</td>
                <td className="py-2 pr-4">20%</td>
                <td className="py-2 pr-4">0% → 0, ≥ 35% → 1</td>
                <td className="py-2">
                  Measures the share of revenue that turns into operating profit:
                  a high margin reflects pricing power or an
                  efficient cost structure.
                </td>
              </tr>
              <tr>
                <td className="py-2 pr-4 font-medium">ROE (or ROIC)</td>
                <td className="py-2 pr-4">25%</td>
                <td className="py-2 pr-4">0% → 0, ≥ 30% → 1</td>
                <td className="py-2">
                  Return on equity over the trailing 12 months (TTM): how much
                  profit the company generates with shareholders&apos; money. ROIC
                  would be preferred (it includes debt in invested capital) but isn&apos;t
                  properly computable with the data available; ROE is used
                  instead.
                </td>
              </tr>
              <tr>
                <td className="py-2 pr-4 font-medium">Net debt / EBITDA</td>
                <td className="py-2 pr-4">15%</td>
                <td className="py-2 pr-4">≤ 0 → 1, ≥ 4x → 0 (inverted)</td>
                <td className="py-2">
                  This ratio is <strong>inverted</strong>: the less debt relative to
                  operating profits, the better. Zero or negative net debt
                  (more cash than debt) gets the maximum score; from 4x annual
                  EBITDA and above, the score drops to 0: the company is judged
                  too indebted for its cash-generating capacity. Not scored for
                  Financial Services companies (banks, insurers): their leverage
                  comes from deposits/float, not operating debt, so the ratio
                  isn&apos;t economically meaningful — the indicator is dropped
                  and the remaining weights re-proportioned, same as missing data.
                </td>
              </tr>
              <tr>
                <td className="py-2 pr-4 font-medium">Positive free cash flow</td>
                <td className="py-2 pr-4">15%</td>
                <td className="py-2 pr-4">Negative → 0, positive → 1 (binary)</td>
                <td className="py-2">
                  FCF (free cash flow) over the trailing 12 months, what&apos;s left
                  after capital expenditures, counts only by its{" "}
                  <em>sign</em>, not its magnitude. Positive FCF means the company
                  self-funds without depending on new debt or share issuance;
                  negative FCF is a warning sign, regardless of sector.
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <p className="text-sm mb-3">
          If data is missing for an indicator (field absent from the data provider), that
          indicator is simply dropped from the calculation and the remaining weights are
          re-proportioned, never replaced by a punitive 0.
        </p>
        <p className="text-sm text-slate-600 dark:text-slate-400">
          <strong>Score of 100</strong>: every available indicator is at its most
          favorable level (growth ≥ 30%, operating margin ≥ 35%, ROE ≥ 30%, more cash than
          debt, positive FCF). <strong>Score of 0</strong>: every available indicator is at its
          most unfavorable level (zero or negative growth, zero margin, zero or negative ROE,
          debt ≥ 4x EBITDA, negative FCF). In practice, most companies fall
          somewhere in between: a pure 100 or 0 score is rare.
        </p>
      </Card>

      <Card title="Risk score" className="scroll-mt-4" id="risk">
        <p className="text-sm mb-4">
          The risk score (0 to 100) combines three metrics computed on price history
          (simple average of the available components):
        </p>
        <ul className="text-sm space-y-3 mb-4">
          <li>
            <strong>Volatility</strong>: how much the stock price has moved, in either
            direction, over the last 5 years. A stock that swings a lot
            day to day (even if it ends up rising) has high volatility. 100 points if
            annualized volatility is ≤ 15%, 0 points if it&apos;s ≥ 60%.
          </li>
          <li>
            <strong>Sharpe ratio</strong>: the return achieved <em>per unit of risk taken</em>
            . Two stocks can have the same return, but the one that achieved it with fewer
            bumps has a better Sharpe. It&apos;s a measure of past efficiency, not a
            prediction: 0 points if Sharpe is ≤ 0 (the return didn&apos;t compensate for the
            risk-free rate), 100 points if Sharpe is ≥ 2.
          </li>
          <li>
            <strong>Max drawdown</strong>: the worst drop ever suffered from a historical peak
            (peak-to-trough) over the period. It&apos;s the most intuitive measure of
            "felt" risk: how much you would have lost, on paper, buying at the worst moment. 100
            points if the worst drop is ≤ 10%, 0 points if it reaches 70% or more.
          </li>
        </ul>
        <p className="text-sm mb-4">
          A fourth metric, the <strong>Sortino ratio</strong>, is also computed and shown
          on the stock page. It works like the Sharpe ratio (return per unit of risk), but
          only counts downside moves (price drops) as "risk," ignoring upside swings. Two
          stocks can have the same Sharpe ratio while one of them mostly swings upward and
          the other mostly downward: the Sortino ratio tells them apart. It is currently
          informational only and does not yet count toward the risk score.
        </p>
        <p className="text-sm font-medium mb-1">
          A high risk score does NOT mean "safe stock."
        </p>
        <p className="text-sm text-slate-600 dark:text-slate-400">
          It means that, <em>historically</em>, the price has been less turbulent and dropped less
          from its peaks than lower-rated stocks, over the observed period only.
          A company can have a high risk score (low past volatility) and
          collapse tomorrow for a reason that doesn&apos;t show up in any of these three
          metrics (fraud, technological disruption, macro event). These metrics describe
          past market behavior, not the company&apos;s soundness or a guarantee about
          the future.
        </p>
      </Card>

      <Card title="Correlation between stocks" className="scroll-mt-4" id="correlation">
        <p className="text-sm mb-3">
          Correlation measures how much two stocks&apos; prices tend to move together over
          time, on a scale from -1 to +1. A value close to +1 means the two prices tend to
          rise and fall together; a value close to 0 means they move mostly independently
          of each other; a value close to -1 means they tend to move in opposite
          directions.
        </p>
        <p className="text-sm mb-3">
          Why it matters: holding several stocks that are all highly correlated does not
          reduce risk much, since they tend to fall together in a downturn. Picking stocks
          with lower correlation to each other spreads risk more effectively, even if each
          one individually looks similar in quality or volatility.
        </p>
        <p className="text-sm text-slate-600 dark:text-slate-400">
          How it&apos;s computed: weekly price changes over the available history for each
          selected stock, compared pairwise. The result is shown as a colored grid (warmer
          colors for higher correlation), available both on the screener page for any group
          of tickers, and on the portfolio page for your own holdings, alongside the average
          correlation and the ticker most correlated to the rest of the group.
        </p>
      </Card>

      <Card title="Position sizing (inverse-volatility weighting)" className="scroll-mt-4" id="weights">
        <p className="text-sm mb-3">
          Once a set of stocks has been chosen, a separate question is how much of the
          portfolio to put into each one. One simple approach: give a larger share to the
          calmer, lower-volatility stocks and a smaller share to the more turbulent ones, so
          that a single very volatile position doesn&apos;t end up dominating the swings of
          the whole portfolio.
        </p>
        <p className="text-sm mb-3">
          In plain terms: each stock&apos;s share is proportional to 1 divided by its
          volatility, and all the shares are then scaled so they add up to 100%. A stock
          whose volatility can&apos;t be measured (not enough price history, or a flat price
          with no movement at all) is simply left out of the calculation rather than forced
          into it with a made-up number.
        </p>
        <p className="text-sm text-slate-600 dark:text-slate-400">
          This is one reasonable way to size positions, not the only one, and it says
          nothing about a stock&apos;s quality: a low-volatility stock isn&apos;t
          necessarily a better company, it just moves less.
        </p>
      </Card>

      <Card title="Efficient frontier (Markowitz)" className="scroll-mt-4" id="frontier">
        <p className="text-sm mb-3">
          For any group of stocks, there is a whole range of possible portfolios depending
          on how much of each stock is held. Some combinations give more expected return for
          the same amount of risk than others; those are called "efficient." Plotting every
          efficient combination gives a curve, with expected return on one axis and
          volatility on the other.
        </p>
        <p className="text-sm mb-3">
          How it&apos;s built: expected return, volatility, and correlation for each stock
          are estimated from historical weekly prices. The tool then picks a range of target
          return levels and, for each one, searches for the mix of stocks (no short selling
          allowed, each stock&apos;s share between 0% and 100%) that reaches that target
          return with the lowest possible volatility. Repeating this across many target
          returns traces the curve.
        </p>
        <p className="text-sm mb-3">
          One point on the curve is highlighted separately: the portfolio with the best
          return-for-risk trade-off (its Sharpe ratio), sometimes called the "tangency"
          portfolio.
        </p>
        <p className="text-sm text-slate-600 dark:text-slate-400">
          Like the risk score, this is a backward-looking estimate built from past prices,
          not a forecast. Past volatility and correlation between stocks are not guaranteed
          to hold in the future, and the curve can shift noticeably if a new stock is added
          or removed from the group.
        </p>
      </Card>

      <Card title="Backtest and periodic rebalancing" className="scroll-mt-4" id="backtest">
        <p className="text-sm mb-3">
          A backtest picks a date in the past, selects a basket of stocks based on the
          composite score as it would have actually been known at that date (using only
          financial reports that were genuinely public by then, so the test never
          accidentally uses information from the future), builds an equally-weighted
          basket, and tracks its performance since then against a benchmark.
        </p>
        <p className="text-sm mb-3">
          By default the basket is built once, at the chosen start date, and held unchanged
          until today. Optionally, the backtest can rebalance periodically instead, every
          month or every quarter. At each of these dates, the composite score is
          recalculated using only the information available at that date, and the
          top-ranked basket is rebuilt from scratch, which can change which stocks are held
          (a stock can drop out of the basket and later come back in, treated each time as a
          fresh purchase).
        </p>
        <p className="text-sm mb-3">
          Every time the basket changes at a rebalancing date, a small transaction cost is
          subtracted, expressed in basis points (hundredths of a percent) of the value
          actually bought or sold; a position that doesn&apos;t change at that date is not
          charged anything. This approximates the real-world drag of trading fees on a
          strategy that adjusts its holdings more often. The backtest reports the
          performance net of these costs, how many rebalances took place, and the total cost
          accumulated over the whole period.
        </p>
        <p className="text-sm text-slate-600 dark:text-slate-400">
          More frequent rebalancing keeps the basket closer to the current top-ranked
          stocks, at the price of paying transaction costs more often; the right frequency
          depends on how much that trade-off matters for a given strategy, there is no
          universally correct choice.
        </p>
      </Card>

      <Card title="Qualitative tally (AI-classified events)" className="scroll-mt-4" id="qualitative">
        <p className="text-sm mb-3">
          Alongside the quantitative scores, the tool surfaces a{" "}
          <strong>qualitative tally</strong>: a count of recent news/filing events, classified by an AI model
          into four categories — dated material contracts, regulatory/litigation events, technical moat
          signals, and mergers &amp; acquisitions. Each event is tagged with a sentiment (positive / negative /
          neutral), a severity, and a confidence level, and is shown in a chronological timeline on each stock
          page.
        </p>
        <p className="text-sm mb-3">
          The compact badge on the dashboard and screener (🟢 / 🔴 / ⚪) is a{" "}
          <strong>count of events over the last 90 days</strong>, excluding low-confidence items from the
          count (those stay visible in the detailed timeline).
        </p>
        <p className="text-sm font-medium mb-1">This tally is not a score.</p>
        <ul className="text-sm space-y-2 text-slate-600 dark:text-slate-400">
          <li>
            It is a <strong>count of events</strong>, not a graded metric — three positive events aren&apos;t
            &quot;better&quot; on any calibrated scale, they are just three events.
          </li>
          <li>
            It is <strong>not backtestable</strong>: events are collected going forward, not reconstructed
            point-in-time, so it can&apos;t be replayed historically like the composite score can.
          </li>
          <li>
            It is <strong>never weighted into the composite score</strong> (nor the fundamental or risk
            scores). The quantitative and qualitative layers are strictly separate and never fused
            arithmetically.
          </li>
          <li>
            The summaries are AI-generated and <strong>may contain extraction errors</strong> (a misread date,
            a misclassified event). Always verify against the linked <code>source</code> before acting on
            anything.
          </li>
        </ul>
      </Card>

      <Card title="Known limitations" className="scroll-mt-4" id="limits">
        <ul className="text-sm space-y-3">
          <li>
            <strong>Weekly prices, not daily.</strong> Price data comes from
            Alpha Vantage; 5-year daily history is a paid endpoint, the
            weekly version is free with full history. All risk metrics
            (volatility, Sharpe) are therefore annualized over 52 periods per year, not 252: they mechanically
            smooth out some intra-week noise compared to a calculation on
            daily closes.
          </li>
          <li>
            <strong>Approximate publication delay (60 days).</strong> To prevent
            a backtest from using, at a given date, a financial report that
            wasn&apos;t yet public at that time ("look-ahead"), each quarterly
            snapshot is considered known as of its fiscal period end date
            plus 60 days. This 60-day delay is an <em>approximation</em>{" "}
            of the actual time between a quarter&apos;s close and the 10-Q/10-K publication: the data
            provider doesn&apos;t supply the exact filing date. For some companies this delay is
            too short, for others too long; it&apos;s not a publication date
            verified case by case.
          </li>
          <li>
            <strong>The score itself has no qualitative input.</strong> The composite, fundamental, and risk
            scores model neither the strength of a competitive advantage (&quot;moat&quot;), nor management
            quality, nor regulatory or legal risk: only figures from financial statements and prices feed the
            scores. The separate{" "}
            <a href="#qualitative" className="text-emerald-600 dark:text-emerald-400 hover:underline">
              qualitative tally
            </a>{" "}
            adds an AI-classified event feed alongside the scores, but is a display-only count and is never
            mixed into any score.
          </li>
          <li>
            <strong>Not a trading tool.</strong> No short-term buy/sell signal,
            no order execution: it&apos;s a screener for long-term
            selection, not an automated decision system.
          </li>
          <li>
            <strong>Correlation and efficient frontier estimates rely on past prices only.</strong>{" "}
            A stock with a short price history gives noisier estimates than one with several
            years of data. Volatility and correlation between stocks can shift quickly,
            especially in stressed markets, so a portfolio that looked well diversified in
            the past isn&apos;t guaranteed to stay that way. The efficient frontier
            calculation also assumes no short selling, and doesn&apos;t account for taxes,
            minimum trade sizes, or how easily a position could actually be traded.
          </li>
          <li>
            <strong>The transaction cost model is simplified.</strong> Rebalancing costs in
            the backtest are a flat rate in basis points applied to the value traded; real
            trading involves bid-ask spreads, slippage, and broker-specific fee schedules
            that this flat rate doesn&apos;t capture, so actual costs could be higher or
            lower depending on the stock and the broker.
          </li>
        </ul>
      </Card>

      <p className="text-xs text-slate-400 dark:text-slate-600">
        This page describes the calculation as it currently runs; it may be refined over
        time as the methodology evolves.
      </p>

      <Link href="/" className="text-sm text-emerald-600 dark:text-emerald-400 hover:underline inline-block">
        &larr; Back to dashboard
      </Link>
    </div>
  );
}
