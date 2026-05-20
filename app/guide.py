from fastapi.responses import HTMLResponse

_GUIDE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>DealerScrapper API &mdash; Guide</title>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: #0d1117; color: #c9d1d9; line-height: 1.6;
    }
    a { color: #58a6ff; text-decoration: none; }
    a:hover { text-decoration: underline; }

    .header {
      background: linear-gradient(135deg, #161b22 0%, #0d1117 100%);
      border-bottom: 1px solid #30363d;
      padding: 2.5rem 2rem 2rem;
      text-align: center;
    }
    .header h1 { font-size: 2rem; color: #f0f6fc; letter-spacing: -0.5px; }
    .header p  { margin-top: 0.4rem; color: #8b949e; font-size: 0.95rem; }
    .header-links {
      margin-top: 1rem;
      display: flex; gap: 1.2rem; justify-content: center; flex-wrap: wrap;
    }
    .header-links a {
      color: #8b949e; font-size: 0.8rem; text-decoration: none;
      display: flex; align-items: center; gap: 0.3rem;
      transition: color 0.15s;
    }
    .header-links a:hover { color: #58a6ff; }
    .badge {
      display: inline-block; margin-top: 0.8rem;
      background: #238636; color: #fff; font-size: 0.75rem;
      padding: 0.2rem 0.7rem; border-radius: 20px; font-weight: 600;
    }

    .container { max-width: 860px; margin: 0 auto; padding: 2rem 1rem 4rem; }

    .lang-picker {
      display: flex; gap: 1rem; justify-content: center; flex-wrap: wrap;
      margin-bottom: 2.5rem;
    }
    .lang-card {
      display: flex; align-items: center; gap: 0.75rem;
      background: #161b22; border: 2px solid #30363d; border-radius: 12px;
      padding: 0.9rem 1.5rem; cursor: pointer;
      transition: border-color 0.15s, background 0.15s;
      user-select: none; flex: 1 1 140px; max-width: 220px;
    }
    .lang-card:hover { border-color: #58a6ff; background: #1c2333; }
    .lang-card.active {
      border-color: #58a6ff; background: #1c2333;
      box-shadow: 0 0 0 3px rgba(88,166,255,.15);
    }
    .lang-card .flag  { font-size: 1.8rem; line-height: 1; }
    .lang-card .label { display: flex; flex-direction: column; }
    .lang-card .label strong { font-size: 0.95rem; color: #f0f6fc; }
    .lang-card .label span   { font-size: 0.75rem; color: #8b949e; }

    .lang-content         { display: none; }
    .lang-content.visible { display: block; }

    .toc {
      background: #161b22; border: 1px solid #30363d; border-radius: 8px;
      padding: 1.25rem 1.5rem; margin-bottom: 2.5rem;
    }
    .toc h2 {
      font-size: 0.8rem; text-transform: uppercase; letter-spacing: 1px;
      color: #8b949e; margin-bottom: 0.75rem;
    }
    .toc ol { padding-left: 1.25rem; }
    .toc li { margin: 0.25rem 0; font-size: 0.9rem; }

    section { margin-bottom: 2.5rem; }
    h2 {
      font-size: 1.2rem; color: #f0f6fc;
      border-bottom: 1px solid #21262d;
      padding-bottom: 0.4rem; margin-bottom: 1rem;
    }
    h3 { font-size: 1rem; color: #e6edf3; margin: 1.25rem 0 0.5rem; }
    h4 { font-size: 0.9rem; color: #e6edf3; margin: 1rem 0 0.4rem; }
    p, li { font-size: 0.9rem; color: #c9d1d9; }
    ul, ol { padding-left: 1.25rem; margin-top: 0.4rem; }
    li { margin: 0.2rem 0; }

    code {
      background: #161b22; border: 1px solid #30363d;
      border-radius: 4px; padding: 0.1rem 0.4rem;
      font-family: "SFMono-Regular", Consolas, monospace; font-size: 0.82rem;
      color: #e6edf3; word-break: break-word;
    }
    pre {
      background: #161b22; border: 1px solid #30363d; border-radius: 8px;
      padding: 1rem 1.25rem; overflow-x: auto; margin-top: 0.75rem;
    }
    pre code {
      background: none; border: none; padding: 0; word-break: normal;
      font-size: 0.82rem; color: #e6edf3; line-height: 1.7;
    }

    .endpoint {
      background: #161b22; border: 1px solid #30363d; border-radius: 8px;
      padding: 1rem 1.25rem; margin-bottom: 1rem;
    }
    .endpoint-header {
      display: flex; align-items: center; flex-wrap: wrap; gap: 0.4rem;
    }
    .method {
      display: inline-block; padding: 0.2rem 0.5rem; border-radius: 4px;
      font-size: 0.75rem; font-weight: 700; margin-right: 0.25rem;
      font-family: monospace; white-space: nowrap;
    }
    .get    { background: #0e4429; color: #3fb950; }
    .post   { background: #1f2d5a; color: #79c0ff; }
    .delete { background: #4a1010; color: #ff7b72; }
    .path { font-family: monospace; font-size: 0.85rem; color: #f0f6fc; word-break: break-all; }
    .auth-badge {
      margin-left: auto; font-size: 0.72rem; padding: 0.15rem 0.5rem;
      border-radius: 12px; border: 1px solid #30363d; color: #8b949e;
      white-space: nowrap;
    }
    .auth-badge.required { border-color: #f0883e; color: #f0883e; }

    .pipeline-flow {
      display: flex; flex-wrap: wrap; gap: 0.3rem;
      align-items: center; margin-top: 0.75rem;
    }
    .phase {
      background: #161b22; border: 1px solid #30363d;
      border-radius: 6px; padding: 0.25rem 0.6rem;
      font-family: monospace; font-size: 0.78rem; color: #79c0ff;
    }
    .phase.terminal-ok  { color: #3fb950; border-color: #238636; }
    .phase.terminal-err { color: #ff7b72; border-color: #6e2020; }
    .arrow { color: #484f58; font-size: 0.85rem; }

    .table-wrap { overflow-x: auto; -webkit-overflow-scrolling: touch; margin-top: 0.75rem; }
    table {
      width: 100%; border-collapse: collapse; font-size: 0.85rem;
      min-width: 400px;
    }
    th {
      background: #161b22; color: #8b949e; text-align: left;
      padding: 0.5rem 0.75rem; border-bottom: 1px solid #30363d;
      font-weight: 600; font-size: 0.78rem; text-transform: uppercase;
      white-space: nowrap;
    }
    td { padding: 0.5rem 0.75rem; border-bottom: 1px solid #21262d; }
    tr:last-child td { border-bottom: none; }
    td code { font-size: 0.78rem; }
    td:first-child { white-space: nowrap; }

    .status-row td:first-child { font-family: monospace; font-weight: 700; }
    .s200 { color: #3fb950; }
    .s201 { color: #3fb950; }
    .s204 { color: #3fb950; }
    .s401 { color: #f0883e; }
    .s404 { color: #d2a8ff; }
    .s425 { color: #79c0ff; }
    .s500 { color: #ff7b72; }

    .note {
      background: #1f2d5a; border-left: 3px solid #79c0ff;
      border-radius: 0 6px 6px 0;
      padding: 0.75rem 1rem; font-size: 0.85rem; margin-top: 0.75rem;
    }
    .warn {
      background: #2d1f0a; border-left: 3px solid #f0883e;
      border-radius: 0 6px 6px 0;
      padding: 0.75rem 1rem; font-size: 0.85rem; margin-top: 0.75rem;
    }

    @media (max-width: 600px) {
      .header h1 { font-size: 1.4rem; }
      .header p  { font-size: 0.85rem; }
      .header-links { gap: 0.8rem; }
      .container { padding: 1.25rem 0.75rem 3rem; }
      .lang-card { padding: 0.75rem 1rem; flex: 1 1 100%; max-width: 100%; }
      .lang-picker { flex-direction: column; align-items: stretch; }
      .endpoint-header { gap: 0.3rem; }
      .auth-badge { margin-left: 0; }
      pre { padding: 0.75rem; }
      th, td { padding: 0.4rem 0.5rem; font-size: 0.78rem; }
      .pipeline-flow { gap: 0.2rem; }
    }
  </style>
</head>
<body>

<div class="header">
  <h1>&#x1F578; DealerScrapper API</h1>
  <p>Async web scraping service &middot; Structured content extraction &middot; REST API reference</p>
  <span class="badge">v1</span>
  <div class="header-links">
    <a href="/guide-ai" target="_blank" rel="noopener">&#x1F916; AI Guide (JSON)</a>
    <a href="https://www.azanolabs.com/" target="_blank" rel="noopener">&#x1F9EA; AzanoLabs</a>
    <a href="https://github.com/azanoRivers" target="_blank" rel="noopener">&#x1F4BB; GitHub</a>
  </div>
</div>

<div class="container">

  <div class="lang-picker">
    <div class="lang-card active" onclick="switchLang('en', this)">
      <span class="flag">&#x1F1FA;&#x1F1F8;</span>
      <div class="label">
        <strong>English</strong>
        <span>Documentation in English</span>
      </div>
    </div>
    <div class="lang-card" onclick="switchLang('es', this)">
      <span class="flag">&#x1F1EA;&#x1F1F8;</span>
      <div class="label">
        <strong>Espa&ntilde;ol</strong>
        <span>Documentaci&oacute;n en espa&ntilde;ol</span>
      </div>
    </div>
  </div>

  <!-- ==================== ENGLISH ==================== -->
  <div id="lang-en" class="lang-content visible">

    <div class="toc">
      <h2>Contents</h2>
      <ol>
        <li><a href="#en-overview">Overview</a></li>
        <li><a href="#en-auth">Authentication</a></li>
        <li><a href="#en-pipeline">Pipeline Flow</a></li>
        <li><a href="#en-endpoints">Endpoints</a></li>
        <li><a href="#en-scrape-params">POST /scrape &mdash; Parameters</a></li>
        <li><a href="#en-statuses">Job Statuses</a></li>
        <li><a href="#en-ttl">TTL &amp; Lifecycle</a></li>
        <li><a href="#en-errors">Error Codes</a></li>
        <li><a href="#en-http">HTTP Status Codes</a></li>
        <li><a href="#en-examples">curl Examples</a></li>
      </ol>
    </div>

    <section id="en-overview">
      <h2>Overview</h2>
      <p>DealerScrapper is an async web scraping API built with FastAPI. It accepts a URL, crawls the site asynchronously, extracts structured content using an LLM, and returns a <code>result.json</code> with business information, content topics, and images.</p>
      <ul>
        <li><strong>Base URL</strong>: <code>https://scraper.azanolabs.com</code></li>
        <li><strong>Port</strong>: <code>8002</code> (proxied via nginx)</li>
        <li><strong>Auth</strong>: <code>X-API-Key</code> header on all <code>/api/v1/</code> endpoints</li>
        <li><strong>Processing</strong>: asynchronous &mdash; create a job, poll for completion, fetch the result</li>
        <li><strong>Max runtime</strong>: 30 minutes per job</li>
        <li><strong>Result TTL</strong>: 15 minutes after completion</li>
      </ul>
    </section>

    <section id="en-auth">
      <h2>Authentication</h2>
      <p>All endpoints under <code>/api/v1/</code> require the <code>X-API-Key</code> header. The <code>GET /</code>, <code>GET /guide</code>, and <code>GET /guide-ai</code> endpoints are public.</p>
      <pre><code>X-API-Key: your-secret-api-key</code></pre>
      <div class="warn">Missing or invalid key returns <code>401 Unauthorized</code> (or <code>422</code> if the header is absent entirely, depending on FastAPI validation).</div>
    </section>

    <section id="en-pipeline">
      <h2>Pipeline Flow</h2>
      <p>Each scraping job runs through a sequential pipeline of sub-agents:</p>
      <div class="pipeline-flow">
        <span class="phase">queued</span>
        <span class="arrow">&rarr;</span>
        <span class="phase">exploring</span>
        <span class="arrow">&rarr;</span>
        <span class="phase">fetching</span>
        <span class="arrow">&rarr;</span>
        <span class="phase">extracting</span>
        <span class="arrow">&rarr;</span>
        <span class="phase">auditing</span>
        <span class="arrow">&rarr;</span>
        <span class="phase">analyzing</span>
        <span class="arrow">&rarr;</span>
        <span class="phase">packaging</span>
        <span class="arrow">&rarr;</span>
        <span class="phase terminal-ok">done</span>
        <span class="arrow">/</span>
        <span class="phase terminal-err">failed</span>
      </div>
      <ul style="margin-top:0.75rem">
        <li><strong>Explorer</strong>: discovers URLs from robots.txt, sitemap.xml, and homepage links</li>
        <li><strong>Fetcher</strong>: downloads HTML pages concurrently (semaphore-limited, with backoff)</li>
        <li><strong>Extractor</strong>: parses HTML into structured PageData using readability-lxml</li>
        <li><strong>Auditor</strong>: checks coverage, triggers a partial re-fetch if gaps are found</li>
        <li><strong>Reviewer</strong>: sends batches of pages to an LLM, builds the final <code>result.json</code></li>
        <li><strong>Packager</strong>: downloads images (optional), creates <code>result.zip</code>, cleans temp files</li>
      </ul>
      <div class="note">Three guards run in parallel: Guard 1 (30 min global timeout), Guard 2 (5 min LLM inactivity watchdog), Guard 3 (15 min TTL cleanup after terminal state).</div>
    </section>

    <section id="en-endpoints">
      <h2>Endpoints</h2>

      <div class="endpoint">
        <div class="endpoint-header">
          <span class="method get">GET</span>
          <span class="path">/guide</span>
          <span class="auth-badge">public</span>
        </div>
        <p style="margin-top:0.5rem;font-size:0.85rem">This page. HTML API reference guide.</p>
      </div>

      <div class="endpoint">
        <div class="endpoint-header">
          <span class="method get">GET</span>
          <span class="path">/guide-ai</span>
          <span class="auth-badge">public</span>
        </div>
        <p style="margin-top:0.5rem;font-size:0.85rem">Machine-readable JSON reference optimized for AI agents (LLMs, LangChain, MCP, etc.).</p>
      </div>

      <div class="endpoint">
        <div class="endpoint-header">
          <span class="method get">GET</span>
          <span class="path">/api/v1/status</span>
          <span class="auth-badge required">X-API-Key required</span>
        </div>
        <p style="margin-top:0.5rem;font-size:0.85rem">Server health: active jobs count, max capacity, version. Returns <code>{ name, version, active_jobs, max_concurrent_jobs, status }</code>.</p>
      </div>

      <div class="endpoint">
        <div class="endpoint-header">
          <span class="method post">POST</span>
          <span class="path">/api/v1/scrape</span>
          <span class="auth-badge required">X-API-Key required</span>
        </div>
        <p style="margin-top:0.5rem;font-size:0.85rem">Create a scraping job. Returns <code>{ job_id, status: "queued" }</code> immediately. Pipeline runs in the background. See <a href="#en-scrape-params">parameters</a> below.</p>
      </div>

      <div class="endpoint">
        <div class="endpoint-header">
          <span class="method get">GET</span>
          <span class="path">/api/v1/scrape/{job_id}/status</span>
          <span class="auth-badge required">X-API-Key required</span>
        </div>
        <p style="margin-top:0.5rem;font-size:0.85rem">Poll job progress. Returns <code>status</code>, <code>progress</code> (phase, pages_done, percent), <code>ttl_remaining_seconds</code>, <code>error</code>, timestamps. Recommended polling interval: every 5&ndash;10 seconds.</p>
      </div>

      <div class="endpoint">
        <div class="endpoint-header">
          <span class="method get">GET</span>
          <span class="path">/api/v1/scrape/{job_id}/result</span>
          <span class="auth-badge required">X-API-Key required</span>
        </div>
        <p style="margin-top:0.5rem;font-size:0.85rem">Fetch the full <code>result.json</code>. Returns <code>425 Too Early</code> if the job is not yet <code>done</code>. Returns <code>404</code> if expired or not found.</p>
      </div>

      <div class="endpoint">
        <div class="endpoint-header">
          <span class="method get">GET</span>
          <span class="path">/api/v1/scrape/{job_id}/images</span>
          <span class="auth-badge required">X-API-Key required</span>
        </div>
        <p style="margin-top:0.5rem;font-size:0.85rem">List downloaded images with per-image <code>download_url</code>, <code>original_url</code>, <code>alt</code>, <code>size_bytes</code>, and the overall <code>ttl_remaining_seconds</code>. Returns <code>404</code> if <code>download_images</code> was false or no images were found.</p>
      </div>

      <div class="endpoint">
        <div class="endpoint-header">
          <span class="method get">GET</span>
          <span class="path">/api/v1/scrape/{job_id}/images/{filename}</span>
          <span class="auth-badge required">X-API-Key required</span>
        </div>
        <p style="margin-top:0.5rem;font-size:0.85rem">Download an individual image file. Content-Type is resolved from the file extension (<code>image/jpeg</code>, <code>image/png</code>, <code>image/webp</code>, etc.).</p>
      </div>

      <div class="endpoint">
        <div class="endpoint-header">
          <span class="method get">GET</span>
          <span class="path">/api/v1/scrape/{job_id}/download</span>
          <span class="auth-badge required">X-API-Key required</span>
        </div>
        <p style="margin-top:0.5rem;font-size:0.85rem">Download <code>result.zip</code> containing <code>result.json</code> + <code>images/</code> (if downloaded). Returns <code>425</code> if job is not <code>done</code>.</p>
      </div>

      <div class="endpoint">
        <div class="endpoint-header">
          <span class="method delete">DELETE</span>
          <span class="path">/api/v1/scrape/{job_id}</span>
          <span class="auth-badge required">X-API-Key required</span>
        </div>
        <p style="margin-top:0.5rem;font-size:0.85rem">Cancel and delete the job immediately. Cancels all active asyncio tasks (Guards 1, 2, 3) and removes the job directory. Returns <code>{ job_id, deleted, message }</code>.</p>
      </div>
    </section>

    <section id="en-scrape-params">
      <h2>POST /api/v1/scrape &mdash; Parameters</h2>
      <p>JSON body:</p>
      <div class="table-wrap"><table>
        <thead><tr><th>Field</th><th>Type</th><th>Required</th><th>Default</th><th>Description</th></tr></thead>
        <tbody>
          <tr><td><code>url</code></td><td>string (URL)</td><td>Yes</td><td>&middot;</td><td>The website to scrape. Must be a valid HTTP/HTTPS URL.</td></tr>
          <tr><td><code>options.max_pages</code></td><td>integer</td><td>No</td><td>50</td><td>Maximum pages to crawl. Useful to limit cost and runtime for large sites.</td></tr>
          <tr><td><code>options.download_images</code></td><td>boolean</td><td>No</td><td>false</td><td>If true, images found in <code>result.json</code> are downloaded locally and included in the ZIP. Enables the <code>/images</code> endpoint.</td></tr>
          <tr><td><code>options.llm_provider</code></td><td>string</td><td>No</td><td>server default</td><td>Override LLM provider: <code>nvidia</code>, <code>openai</code>, <code>anthropic</code>, <code>deepseek</code>, <code>minimax</code>.</td></tr>
          <tr><td><code>options.llm_model</code></td><td>string</td><td>No</td><td>server default</td><td>Override specific model name (e.g. <code>moonshotai/kimi-k2.6</code>, <code>gpt-4o</code>, <code>claude-3-5-haiku-20241022</code>).</td></tr>
        </tbody>
      </table></div>
      <h3>Response</h3>
      <pre><code>{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "queued"
}</code></pre>
    </section>

    <section id="en-statuses">
      <h2>Job Statuses</h2>
      <div class="table-wrap"><table>
        <thead><tr><th>Status</th><th>Meaning</th></tr></thead>
        <tbody>
          <tr><td><code>queued</code></td><td>Job created, pipeline not yet started</td></tr>
          <tr><td><code>exploring</code></td><td>Explorer discovering routes (robots.txt, sitemap, links)</td></tr>
          <tr><td><code>fetching</code></td><td>Fetcher downloading HTML pages (numeric progress available)</td></tr>
          <tr><td><code>extracting</code></td><td>Extractor parsing HTML into structured PageData</td></tr>
          <tr><td><code>auditing</code></td><td>Auditor checking coverage, possibly triggering re-fetch</td></tr>
          <tr><td><code>analyzing</code></td><td>Reviewer + LLM building final structure (LLM watchdog active)</td></tr>
          <tr><td><code>packaging</code></td><td>Packager creating result.zip, cleaning temp files</td></tr>
          <tr><td><code>done</code></td><td>Completed successfully &mdash; TTL of 15 min running</td></tr>
          <tr><td><code>failed</code></td><td>Terminal error &mdash; TTL of 15 min running, see <code>error_code</code></td></tr>
          <tr><td><code>expired</code></td><td>Job existed but TTL elapsed and was deleted</td></tr>
        </tbody>
      </table></div>
      <h3>Status response shape</h3>
      <pre><code>{
  "job_id": "550e8400-...",
  "status": "fetching",
  "progress": {
    "phase": "fetching",
    "pages_done": 12,
    "pages_total": 30,
    "percent": 40
  },
  "ttl_remaining_seconds": null,   // null until done/failed; then counts down from 900
  "error": null,
  "created_at": "2026-05-19T10:00:00Z",
  "started_at": "2026-05-19T10:00:01Z",
  "updated_at": "2026-05-19T10:01:05Z",
  "done_at": null,
  "estimated_remaining_seconds": 45
}</code></pre>
    </section>

    <section id="en-ttl">
      <h2>TTL &amp; Lifecycle</h2>
      <ul>
        <li>Results (files, images, ZIP) are kept for <strong>15 minutes</strong> after the job reaches <code>done</code> or <code>failed</code>.</li>
        <li><code>ttl_remaining_seconds</code> is <code>null</code> while the job is running; starts counting down from <strong>900</strong> when the job completes.</li>
        <li>After TTL expires, the job directory is deleted automatically. Subsequent requests return <code>404 job_not_found</code>.</li>
        <li>Guard 3 runs in the background and handles the cleanup. <code>DELETE /{job_id}</code> can cancel it explicitly.</li>
      </ul>
      <div class="note"><strong>Recommended flow:</strong> poll <code>/status</code> until <code>done</code>, then immediately fetch <code>/result</code> or <code>/download</code>. Do not wait &mdash; the TTL starts running the moment the job completes.</div>
    </section>

    <section id="en-errors">
      <h2>Error Codes</h2>
      <p>When a job fails, <code>GET /status</code> returns <code>status: "failed"</code> with an <code>error</code> object containing <code>code</code>, <code>message</code>, and <code>retry_after</code>.</p>
      <div class="table-wrap"><table>
        <thead><tr><th>Code</th><th>Cause</th><th>retry_after</th><th>Action</th></tr></thead>
        <tbody>
          <tr><td><code>NO_ROUTES_FOUND</code></td><td>JS-only site without SSR or blocked the crawler</td><td>null</td><td>Notify user; site requires JS rendering</td></tr>
          <tr><td><code>FETCH_ALL_FAILED</code></td><td>All pages returned 4xx/5xx or timed out</td><td>300 s</td><td>Retry later</td></tr>
          <tr><td><code>EXTRACTION_EMPTY</code></td><td>Downloaded HTML had no extractable content (JS-rendered)</td><td>null</td><td>Notify: site requires JS</td></tr>
          <tr><td><code>AUDIT_CRITICAL_GAPS</code></td><td>Coverage below minimum threshold after re-fetch</td><td>null</td><td>Partial result may apply</td></tr>
          <tr><td><code>LLM_TIMEOUT</code></td><td>LLM model inactive for more than 5 minutes</td><td>300 s</td><td>Retry; verify API credits</td></tr>
          <tr><td><code>LLM_AUTH_ERROR</code></td><td>Invalid API key or no credits</td><td>null</td><td>Do not retry; fix config</td></tr>
          <tr><td><code>LLM_PARSE_ERROR</code></td><td>Malformed JSON response after 2 retries</td><td>60 s</td><td>Can retry</td></tr>
          <tr><td><code>JOB_TIMEOUT</code></td><td>Job exceeded 30-minute global timeout</td><td>600 s</td><td>Retry with smaller <code>max_pages</code></td></tr>
          <tr><td><code>INTERNAL_ERROR</code></td><td>Unexpected server error</td><td>60 s</td><td>Report to admin</td></tr>
        </tbody>
      </table></div>
      <h3>Error response shape (from /status)</h3>
      <pre><code>{
  "job_id": "550e8400-...",
  "status": "failed",
  "error": {
    "code": "LLM_TIMEOUT",
    "message": "El modelo LLM no produjo actividad en 5 minutos.",
    "retry_after": 300
  },
  ...
}</code></pre>
    </section>

    <section id="en-http">
      <h2>HTTP Status Codes</h2>
      <div class="table-wrap"><table>
        <thead><tr><th>Code</th><th>Meaning</th></tr></thead>
        <tbody class="status-row">
          <tr><td class="s200">200</td><td>OK &mdash; request succeeded</td></tr>
          <tr><td class="s201">201</td><td>Created &mdash; job created (<code>POST /scrape</code>)</td></tr>
          <tr><td class="s401">401</td><td>Unauthorized &mdash; invalid or missing <code>X-API-Key</code></td></tr>
          <tr><td class="s404">404</td><td>Not found &mdash; job does not exist or TTL expired</td></tr>
          <tr><td class="s425">425</td><td>Too Early &mdash; job not yet <code>done</code> (for <code>/result</code> and <code>/download</code>)</td></tr>
          <tr><td class="s500">500</td><td>Internal Server Error &mdash; unexpected failure</td></tr>
        </tbody>
      </table></div>
    </section>

    <section id="en-examples">
      <h2>curl Examples</h2>
      <h3>Create a scraping job</h3>
      <pre><code>curl -X POST https://scraper.azanolabs.com/api/v1/scrape \\
  -H "X-API-Key: your-key" \\
  -H "Content-Type: application/json" \\
  -d '{"url": "https://example-dealer.com"}'</code></pre>

      <h3>Create a job with image download</h3>
      <pre><code>curl -X POST https://scraper.azanolabs.com/api/v1/scrape \\
  -H "X-API-Key: your-key" \\
  -H "Content-Type: application/json" \\
  -d '{
    "url": "https://example-dealer.com",
    "options": {
      "download_images": true,
      "max_pages": 20
    }
  }'</code></pre>

      <h3>Poll status</h3>
      <pre><code>curl https://scraper.azanolabs.com/api/v1/scrape/JOB_ID/status \\
  -H "X-API-Key: your-key"</code></pre>

      <h3>Fetch result (once done)</h3>
      <pre><code>curl https://scraper.azanolabs.com/api/v1/scrape/JOB_ID/result \\
  -H "X-API-Key: your-key"</code></pre>

      <h3>Download ZIP</h3>
      <pre><code>curl https://scraper.azanolabs.com/api/v1/scrape/JOB_ID/download \\
  -H "X-API-Key: your-key" \\
  --output result.zip</code></pre>

      <h3>List images</h3>
      <pre><code>curl https://scraper.azanolabs.com/api/v1/scrape/JOB_ID/images \\
  -H "X-API-Key: your-key"</code></pre>

      <h3>Download a specific image</h3>
      <pre><code>curl https://scraper.azanolabs.com/api/v1/scrape/JOB_ID/images/img_001.jpg \\
  -H "X-API-Key: your-key" \\
  --output img_001.jpg</code></pre>

      <h3>Delete / cancel a job</h3>
      <pre><code>curl -X DELETE https://scraper.azanolabs.com/api/v1/scrape/JOB_ID \\
  -H "X-API-Key: your-key"</code></pre>

      <h3>Full polling loop (bash)</h3>
      <pre><code>JOB_ID=$(curl -s -X POST https://scraper.azanolabs.com/api/v1/scrape \\
  -H "X-API-Key: your-key" -H "Content-Type: application/json" \\
  -d '{"url":"https://example-dealer.com"}' | jq -r '.job_id')

while true; do
  STATUS=$(curl -s "https://scraper.azanolabs.com/api/v1/scrape/$JOB_ID/status" \\
    -H "X-API-Key: your-key" | jq -r '.status')
  echo "Status: $STATUS"
  [ "$STATUS" = "done" ] || [ "$STATUS" = "failed" ] && break
  sleep 8
done

curl -s "https://scraper.azanolabs.com/api/v1/scrape/$JOB_ID/result" \\
  -H "X-API-Key: your-key" | jq .</code></pre>
    </section>

  </div><!-- #lang-en -->

  <!-- ==================== ESPA&Ntilde;OL ==================== -->
  <div id="lang-es" class="lang-content">

    <div class="toc">
      <h2>Contenido</h2>
      <ol>
        <li><a href="#es-overview">Resumen</a></li>
        <li><a href="#es-auth">Autenticaci&oacute;n</a></li>
        <li><a href="#es-pipeline">Flujo del Pipeline</a></li>
        <li><a href="#es-endpoints">Endpoints</a></li>
        <li><a href="#es-scrape-params">POST /scrape &mdash; Par&aacute;metros</a></li>
        <li><a href="#es-statuses">Estados del Job</a></li>
        <li><a href="#es-ttl">TTL y Ciclo de Vida</a></li>
        <li><a href="#es-errors">C&oacute;digos de Error</a></li>
        <li><a href="#es-http">C&oacute;digos HTTP</a></li>
        <li><a href="#es-examples">Ejemplos con curl</a></li>
      </ol>
    </div>

    <section id="es-overview">
      <h2>Resumen</h2>
      <p>DealerScrapper es una API de web scraping as&iacute;ncrona construida con FastAPI. Acepta una URL, rastrea el sitio de forma as&iacute;ncrona, extrae contenido estructurado usando un LLM y devuelve un <code>result.json</code> con informaci&oacute;n del negocio, temas de contenido e im&aacute;genes.</p>
      <ul>
        <li><strong>URL base</strong>: <code>https://scraper.azanolabs.com</code></li>
        <li><strong>Puerto</strong>: <code>8002</code> (proxiado por nginx)</li>
        <li><strong>Auth</strong>: header <code>X-API-Key</code> en todos los endpoints <code>/api/v1/</code></li>
        <li><strong>Procesamiento</strong>: as&iacute;ncrono &mdash; cre&aacute; un job, poll&eacute; hasta completar, obten&eacute; el resultado</li>
        <li><strong>Tiempo m&aacute;ximo</strong>: 30 minutos por job</li>
        <li><strong>TTL del resultado</strong>: 15 minutos tras completar</li>
      </ul>
    </section>

    <section id="es-auth">
      <h2>Autenticaci&oacute;n</h2>
      <p>Todos los endpoints bajo <code>/api/v1/</code> requieren el header <code>X-API-Key</code>. Los endpoints <code>GET /</code>, <code>GET /guide</code> y <code>GET /guide-ai</code> son p&uacute;blicos.</p>
      <pre><code>X-API-Key: tu-clave-secreta</code></pre>
      <div class="warn">Clave ausente o inv&aacute;lida devuelve <code>401 Unauthorized</code> (o <code>422</code> si el header falta por completo, seg&uacute;n la validaci&oacute;n de FastAPI).</div>
    </section>

    <section id="es-pipeline">
      <h2>Flujo del Pipeline</h2>
      <p>Cada job de scraping corre una pipeline secuencial de sub-agentes:</p>
      <div class="pipeline-flow">
        <span class="phase">queued</span>
        <span class="arrow">&rarr;</span>
        <span class="phase">exploring</span>
        <span class="arrow">&rarr;</span>
        <span class="phase">fetching</span>
        <span class="arrow">&rarr;</span>
        <span class="phase">extracting</span>
        <span class="arrow">&rarr;</span>
        <span class="phase">auditing</span>
        <span class="arrow">&rarr;</span>
        <span class="phase">analyzing</span>
        <span class="arrow">&rarr;</span>
        <span class="phase">packaging</span>
        <span class="arrow">&rarr;</span>
        <span class="phase terminal-ok">done</span>
        <span class="arrow">/</span>
        <span class="phase terminal-err">failed</span>
      </div>
      <ul style="margin-top:0.75rem">
        <li><strong>Explorer</strong>: descubre URLs desde robots.txt, sitemap.xml y links del homepage</li>
        <li><strong>Fetcher</strong>: descarga p&aacute;ginas HTML concurrentemente (sem&aacute;foro + backoff)</li>
        <li><strong>Extractor</strong>: parsea HTML a PageData estructurado usando readability-lxml</li>
        <li><strong>Auditor</strong>: verifica cobertura, dispara re-fetch parcial si hay huecos</li>
        <li><strong>Reviewer</strong>: env&iacute;a lotes de p&aacute;ginas al LLM y construye el <code>result.json</code> final</li>
        <li><strong>Packager</strong>: descarga im&aacute;genes (opcional), crea <code>result.zip</code>, limpia temporales</li>
      </ul>
      <div class="note">Tres guards corren en paralelo: Guard 1 (timeout global 30 min), Guard 2 (watchdog de inactividad LLM 5 min), Guard 3 (cleanup TTL 15 min tras estado terminal).</div>
    </section>

    <section id="es-endpoints">
      <h2>Endpoints</h2>

      <div class="endpoint">
        <div class="endpoint-header">
          <span class="method get">GET</span>
          <span class="path">/guide</span>
          <span class="auth-badge">p&uacute;blico</span>
        </div>
        <p style="margin-top:0.5rem;font-size:0.85rem">Esta p&aacute;gina. Referencia HTML de la API.</p>
      </div>

      <div class="endpoint">
        <div class="endpoint-header">
          <span class="method get">GET</span>
          <span class="path">/guide-ai</span>
          <span class="auth-badge">p&uacute;blico</span>
        </div>
        <p style="margin-top:0.5rem;font-size:0.85rem">Referencia JSON optimizada para agentes de IA (LLMs, LangChain, MCP, etc.).</p>
      </div>

      <div class="endpoint">
        <div class="endpoint-header">
          <span class="method get">GET</span>
          <span class="path">/api/v1/status</span>
          <span class="auth-badge required">X-API-Key requerido</span>
        </div>
        <p style="margin-top:0.5rem;font-size:0.85rem">Estado del servidor: jobs activos, capacidad, versi&oacute;n. Devuelve <code>{ name, version, active_jobs, max_concurrent_jobs, status }</code>.</p>
      </div>

      <div class="endpoint">
        <div class="endpoint-header">
          <span class="method post">POST</span>
          <span class="path">/api/v1/scrape</span>
          <span class="auth-badge required">X-API-Key requerido</span>
        </div>
        <p style="margin-top:0.5rem;font-size:0.85rem">Crea un job de scraping. Devuelve <code>{ job_id, status: "queued" }</code> inmediatamente. La pipeline corre en segundo plano. Ver <a href="#es-scrape-params">par&aacute;metros</a>.</p>
      </div>

      <div class="endpoint">
        <div class="endpoint-header">
          <span class="method get">GET</span>
          <span class="path">/api/v1/scrape/{job_id}/status</span>
          <span class="auth-badge required">X-API-Key requerido</span>
        </div>
        <p style="margin-top:0.5rem;font-size:0.85rem">Consulta el progreso del job. Devuelve <code>status</code>, <code>progress</code> (phase, pages_done, percent), <code>ttl_remaining_seconds</code>, <code>error</code>, timestamps. Intervalo recomendado: cada 5&ndash;10 segundos.</p>
      </div>

      <div class="endpoint">
        <div class="endpoint-header">
          <span class="method get">GET</span>
          <span class="path">/api/v1/scrape/{job_id}/result</span>
          <span class="auth-badge required">X-API-Key requerido</span>
        </div>
        <p style="margin-top:0.5rem;font-size:0.85rem">Obtiene el <code>result.json</code> completo. Devuelve <code>425 Too Early</code> si el job a&uacute;n no est&aacute; <code>done</code>. Devuelve <code>404</code> si expir&oacute; o no existe.</p>
      </div>

      <div class="endpoint">
        <div class="endpoint-header">
          <span class="method get">GET</span>
          <span class="path">/api/v1/scrape/{job_id}/images</span>
          <span class="auth-badge required">X-API-Key requerido</span>
        </div>
        <p style="margin-top:0.5rem;font-size:0.85rem">Lista las im&aacute;genes descargadas con <code>download_url</code>, <code>original_url</code>, <code>alt</code>, <code>size_bytes</code> por imagen y <code>ttl_remaining_seconds</code> global. Devuelve <code>404</code> si <code>download_images</code> era false o no se encontraron im&aacute;genes.</p>
      </div>

      <div class="endpoint">
        <div class="endpoint-header">
          <span class="method get">GET</span>
          <span class="path">/api/v1/scrape/{job_id}/images/{filename}</span>
          <span class="auth-badge required">X-API-Key requerido</span>
        </div>
        <p style="margin-top:0.5rem;font-size:0.85rem">Descarga una imagen individual. El Content-Type se resuelve por extensi&oacute;n (<code>image/jpeg</code>, <code>image/png</code>, <code>image/webp</code>, etc.).</p>
      </div>

      <div class="endpoint">
        <div class="endpoint-header">
          <span class="method get">GET</span>
          <span class="path">/api/v1/scrape/{job_id}/download</span>
          <span class="auth-badge required">X-API-Key requerido</span>
        </div>
        <p style="margin-top:0.5rem;font-size:0.85rem">Descarga <code>result.zip</code> con <code>result.json</code> + <code>images/</code> (si se descargaron). Devuelve <code>425</code> si el job a&uacute;n no est&aacute; <code>done</code>.</p>
      </div>

      <div class="endpoint">
        <div class="endpoint-header">
          <span class="method delete">DELETE</span>
          <span class="path">/api/v1/scrape/{job_id}</span>
          <span class="auth-badge required">X-API-Key requerido</span>
        </div>
        <p style="margin-top:0.5rem;font-size:0.85rem">Cancela y elimina el job inmediatamente. Cancela todas las Tasks asyncio activas (Guards 1, 2, 3) y elimina el directorio del job. Devuelve <code>{ job_id, deleted, message }</code>.</p>
      </div>
    </section>

    <section id="es-scrape-params">
      <h2>POST /api/v1/scrape &mdash; Par&aacute;metros</h2>
      <p>Cuerpo JSON:</p>
      <div class="table-wrap"><table>
        <thead><tr><th>Campo</th><th>Tipo</th><th>Requerido</th><th>Default</th><th>Descripci&oacute;n</th></tr></thead>
        <tbody>
          <tr><td><code>url</code></td><td>string (URL)</td><td>S&iacute;</td><td>&middot;</td><td>El sitio web a scrapear. Debe ser una URL HTTP/HTTPS v&aacute;lida.</td></tr>
          <tr><td><code>options.max_pages</code></td><td>entero</td><td>No</td><td>50</td><td>M&aacute;ximo de p&aacute;ginas a rastrear. &Uacute;til para limitar costo y tiempo en sitios grandes.</td></tr>
          <tr><td><code>options.download_images</code></td><td>boolean</td><td>No</td><td>false</td><td>Si true, las im&aacute;genes del <code>result.json</code> se descargan localmente y se incluyen en el ZIP. Habilita el endpoint <code>/images</code>.</td></tr>
          <tr><td><code>options.llm_provider</code></td><td>string</td><td>No</td><td>default del servidor</td><td>Override del provider LLM: <code>nvidia</code>, <code>openai</code>, <code>anthropic</code>, <code>deepseek</code>, <code>minimax</code>.</td></tr>
          <tr><td><code>options.llm_model</code></td><td>string</td><td>No</td><td>default del servidor</td><td>Override del nombre de modelo (ej. <code>moonshotai/kimi-k2.6</code>, <code>gpt-4o</code>, <code>claude-3-5-haiku-20241022</code>).</td></tr>
        </tbody>
      </table></div>
      <h3>Respuesta</h3>
      <pre><code>{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "queued"
}</code></pre>
    </section>

    <section id="es-statuses">
      <h2>Estados del Job</h2>
      <div class="table-wrap"><table>
        <thead><tr><th>Estado</th><th>Significado</th></tr></thead>
        <tbody>
          <tr><td><code>queued</code></td><td>Job creado, pipeline a&uacute;n no iniciada</td></tr>
          <tr><td><code>exploring</code></td><td>Explorer descubriendo rutas (robots.txt, sitemap, links)</td></tr>
          <tr><td><code>fetching</code></td><td>Fetcher descargando p&aacute;ginas HTML (progreso num&eacute;rico disponible)</td></tr>
          <tr><td><code>extracting</code></td><td>Extractor parseando HTML a PageData estructurado</td></tr>
          <tr><td><code>auditing</code></td><td>Auditor verificando cobertura, posiblemente disparando re-fetch</td></tr>
          <tr><td><code>analyzing</code></td><td>Reviewer + LLM construyendo estructura final (watchdog LLM activo)</td></tr>
          <tr><td><code>packaging</code></td><td>Packager creando result.zip, limpiando temporales</td></tr>
          <tr><td><code>done</code></td><td>Completado con &eacute;xito &mdash; TTL de 15 min corriendo</td></tr>
          <tr><td><code>failed</code></td><td>Error terminal &mdash; TTL de 15 min corriendo, ver <code>error_code</code></td></tr>
          <tr><td><code>expired</code></td><td>El job existi&oacute; pero el TTL venci&oacute; y fue eliminado</td></tr>
        </tbody>
      </table></div>
      <h3>Forma de la respuesta de estado</h3>
      <pre><code>{
  "job_id": "550e8400-...",
  "status": "fetching",
  "progress": {
    "phase": "fetching",
    "pages_done": 12,
    "pages_total": 30,
    "percent": 40
  },
  "ttl_remaining_seconds": null,   // null mientras corre; cuenta regresiva desde 900 al terminar
  "error": null,
  "created_at": "2026-05-19T10:00:00Z",
  "started_at": "2026-05-19T10:00:01Z",
  "updated_at": "2026-05-19T10:01:05Z",
  "done_at": null,
  "estimated_remaining_seconds": 45
}</code></pre>
    </section>

    <section id="es-ttl">
      <h2>TTL y Ciclo de Vida</h2>
      <ul>
        <li>Los resultados (archivos, im&aacute;genes, ZIP) se conservan <strong>15 minutos</strong> despu&eacute;s de que el job llega a <code>done</code> o <code>failed</code>.</li>
        <li><code>ttl_remaining_seconds</code> es <code>null</code> mientras el job corre; empieza a decrementar desde <strong>900</strong> al completar.</li>
        <li>Al vencer el TTL, el directorio del job se elimina autom&aacute;ticamente. Las peticiones posteriores devuelven <code>404 job_not_found</code>.</li>
        <li>Guard 3 corre en segundo plano y gestiona la limpieza. <code>DELETE /{job_id}</code> puede cancelarlo expl&iacute;citamente.</li>
      </ul>
      <div class="note"><strong>Flujo recomendado:</strong> poll&eacute; <code>/status</code> hasta <code>done</code>, luego obten&eacute; inmediatamente <code>/result</code> o <code>/download</code>. No esperar&mdash;el TTL empieza a correr en el momento en que el job completa.</div>
    </section>

    <section id="es-errors">
      <h2>C&oacute;digos de Error</h2>
      <p>Cuando un job falla, <code>GET /status</code> devuelve <code>status: "failed"</code> con un objeto <code>error</code> que contiene <code>code</code>, <code>message</code> y <code>retry_after</code>.</p>
      <div class="table-wrap"><table>
        <thead><tr><th>C&oacute;digo</th><th>Causa</th><th>retry_after</th><th>Acci&oacute;n</th></tr></thead>
        <tbody>
          <tr><td><code>NO_ROUTES_FOUND</code></td><td>Sitio JS-only sin SSR o bloque&oacute; el crawler</td><td>null</td><td>Notificar al usuario; sitio requiere JS</td></tr>
          <tr><td><code>FETCH_ALL_FAILED</code></td><td>Todas las p&aacute;ginas devolvieron 4xx/5xx o timeout</td><td>300 s</td><td>Reintentar m&aacute;s tarde</td></tr>
          <tr><td><code>EXTRACTION_EMPTY</code></td><td>HTML descargado sin contenido extraible (JS-rendered)</td><td>null</td><td>Notificar: sitio requiere JS</td></tr>
          <tr><td><code>AUDIT_CRITICAL_GAPS</code></td><td>Cobertura bajo umbral m&iacute;nimo tras re-fetch</td><td>null</td><td>Resultado parcial si aplica</td></tr>
          <tr><td><code>LLM_TIMEOUT</code></td><td>Modelo LLM inactivo m&aacute;s de 5 minutos</td><td>300 s</td><td>Reintentar; verificar cr&eacute;ditos</td></tr>
          <tr><td><code>LLM_AUTH_ERROR</code></td><td>API key inv&aacute;lida o sin cr&eacute;ditos</td><td>null</td><td>No reintentar; corregir config</td></tr>
          <tr><td><code>LLM_PARSE_ERROR</code></td><td>JSON malformado tras 2 reintentos</td><td>60 s</td><td>Puede reintentar</td></tr>
          <tr><td><code>JOB_TIMEOUT</code></td><td>Job super&oacute; el timeout global de 30 minutos</td><td>600 s</td><td>Reintentar con <code>max_pages</code> menor</td></tr>
          <tr><td><code>INTERNAL_ERROR</code></td><td>Error inesperado del servidor</td><td>60 s</td><td>Reportar al administrador</td></tr>
        </tbody>
      </table></div>
      <h3>Forma del objeto de error (desde /status)</h3>
      <pre><code>{
  "job_id": "550e8400-...",
  "status": "failed",
  "error": {
    "code": "LLM_TIMEOUT",
    "message": "El modelo LLM no produjo actividad en 5 minutos.",
    "retry_after": 300
  },
  ...
}</code></pre>
    </section>

    <section id="es-http">
      <h2>C&oacute;digos HTTP</h2>
      <div class="table-wrap"><table>
        <thead><tr><th>C&oacute;digo</th><th>Significado</th></tr></thead>
        <tbody class="status-row">
          <tr><td class="s200">200</td><td>OK &mdash; petici&oacute;n exitosa</td></tr>
          <tr><td class="s201">201</td><td>Created &mdash; job creado (<code>POST /scrape</code>)</td></tr>
          <tr><td class="s401">401</td><td>Unauthorized &mdash; <code>X-API-Key</code> inv&aacute;lido o ausente</td></tr>
          <tr><td class="s404">404</td><td>Not found &mdash; job no existe o TTL venci&oacute;</td></tr>
          <tr><td class="s425">425</td><td>Too Early &mdash; job a&uacute;n no est&aacute; <code>done</code> (para <code>/result</code> y <code>/download</code>)</td></tr>
          <tr><td class="s500">500</td><td>Internal Server Error &mdash; fallo inesperado</td></tr>
        </tbody>
      </table></div>
    </section>

    <section id="es-examples">
      <h2>Ejemplos con curl</h2>
      <h3>Crear un job de scraping</h3>
      <pre><code>curl -X POST https://scraper.azanolabs.com/api/v1/scrape \\
  -H "X-API-Key: tu-clave" \\
  -H "Content-Type: application/json" \\
  -d '{"url": "https://example-dealer.com"}'</code></pre>

      <h3>Crear un job con descarga de im&aacute;genes</h3>
      <pre><code>curl -X POST https://scraper.azanolabs.com/api/v1/scrape \\
  -H "X-API-Key: tu-clave" \\
  -H "Content-Type: application/json" \\
  -d '{
    "url": "https://example-dealer.com",
    "options": {
      "download_images": true,
      "max_pages": 20
    }
  }'</code></pre>

      <h3>Consultar estado</h3>
      <pre><code>curl https://scraper.azanolabs.com/api/v1/scrape/JOB_ID/status \\
  -H "X-API-Key: tu-clave"</code></pre>

      <h3>Obtener resultado (una vez done)</h3>
      <pre><code>curl https://scraper.azanolabs.com/api/v1/scrape/JOB_ID/result \\
  -H "X-API-Key: tu-clave"</code></pre>

      <h3>Descargar ZIP</h3>
      <pre><code>curl https://scraper.azanolabs.com/api/v1/scrape/JOB_ID/download \\
  -H "X-API-Key: tu-clave" \\
  --output result.zip</code></pre>

      <h3>Listar im&aacute;genes</h3>
      <pre><code>curl https://scraper.azanolabs.com/api/v1/scrape/JOB_ID/images \\
  -H "X-API-Key: tu-clave"</code></pre>

      <h3>Descargar una imagen espec&iacute;fica</h3>
      <pre><code>curl https://scraper.azanolabs.com/api/v1/scrape/JOB_ID/images/img_001.jpg \\
  -H "X-API-Key: tu-clave" \\
  --output img_001.jpg</code></pre>

      <h3>Cancelar / eliminar un job</h3>
      <pre><code>curl -X DELETE https://scraper.azanolabs.com/api/v1/scrape/JOB_ID \\
  -H "X-API-Key: tu-clave"</code></pre>

      <h3>Loop de polling completo (bash)</h3>
      <pre><code>JOB_ID=$(curl -s -X POST https://scraper.azanolabs.com/api/v1/scrape \\
  -H "X-API-Key: tu-clave" -H "Content-Type: application/json" \\
  -d '{"url":"https://example-dealer.com"}' | jq -r '.job_id')

while true; do
  STATUS=$(curl -s "https://scraper.azanolabs.com/api/v1/scrape/$JOB_ID/status" \\
    -H "X-API-Key: tu-clave" | jq -r '.status')
  echo "Estado: $STATUS"
  [ "$STATUS" = "done" ] || [ "$STATUS" = "failed" ] && break
  sleep 8
done

curl -s "https://scraper.azanolabs.com/api/v1/scrape/$JOB_ID/result" \\
  -H "X-API-Key: tu-clave" | jq .</code></pre>
    </section>

  </div><!-- #lang-es -->

</div><!-- .container -->

<script>
  function switchLang(lang, card) {
    document.querySelectorAll('.lang-card').forEach(function(c) { c.classList.remove('active'); });
    card.classList.add('active');
    document.querySelectorAll('.lang-content').forEach(function(el) { el.classList.remove('visible'); });
    document.getElementById('lang-' + lang).classList.add('visible');
    document.documentElement.lang = lang;
  }
</script>

</body>
</html>"""


def get_guide() -> HTMLResponse:
    return HTMLResponse(content=_GUIDE_HTML)
