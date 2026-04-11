(function () {
  const knowledge = window.CODELITE_KNOWLEDGE;
  if (!knowledge) {
    document.body.innerHTML = "<p>Knowledge data failed to load.</p>";
    return;
  }

  const pageConfig = {
    home: {
      label: "项目总览",
      note: "定位、亮点、时间线、复习顺序",
      kicker: "Interview Dossier",
      title: "先把项目讲对，再讲复杂。",
      intro: "这一页用来建立项目主叙事。你需要先让面试官明白 CodeLite 是什么、不是什么、为什么值得讲，然后再进入架构和机制细节。"
    },
    architecture: {
      label: "架构拆解",
      note: "分层、主调用链、关键设计选择",
      kicker: "Architecture Map",
      title: "把代码结构讲成系统结构。",
      intro: "面试里最怕堆模块名。这一页按入口、组合根、执行核心、安全边界、隔离执行、治理层和扩展层来讲，能把代码模块提升为系统叙事。"
    },
    mechanisms: {
      label: "核心机制",
      note: "按问题、设计、证据来理解",
      kicker: "Mechanism Deck",
      title: "每一项能力都要回答三个问题。",
      intro: "它解决了什么问题？为什么这样设计？有什么代码和测试能证明？如果你能把每张卡讲清楚，深挖时就不会虚。"
    },
    interview: {
      label: "面试问答",
      note: "高频问题、标准答法、追问链路",
      kicker: "Question Bank",
      title: "别背八股，要背自己的证据链。",
      intro: "这里的问题全部围绕本项目真实实现组织。建议先看 30 秒答法，再顺着 2 分钟展开练几轮，把 follow-up 也练熟。"
    },
    resume: {
      label: "简历与讲稿",
      note: "简历条目、STAR、3/5/10 分钟脚本",
      kicker: "Presentation Layer",
      title: "写在简历上的每个词，都要能展开。",
      intro: "这一页解决两个问题：简历怎么写不空，口头怎么讲不散。推荐先记关键词，再记 3 分钟版，最后补 STAR 和 10 分钟深挖稿。"
    },
    graph: {
      label: "知识图谱",
      note: "模块关系、证据链、讲解导航",
      kicker: "Knowledge Graph",
      title: "从节点关系里记住整个项目。",
      intro: "图谱视图用来串联入口、核心、隔离、安全、治理、智能增强、扩展和证据层。点击节点后，只记两件事：它做什么，它和谁协作。"
    }
  };

  const typePalette = {
    entry: "#b13f1e",
    core: "#1f5f94",
    safety: "#7b2cbf",
    isolation: "#c05b00",
    governance: "#0d6f64",
    dispatch: "#8a5a00",
    intelligence: "#2d4c8d",
    extension: "#8a2f50",
    quality: "#355f26",
    evidence: "#4c4c4c",
    presentation: "#9c324b"
  };

  const page = document.body.dataset.page || "home";
  const app = document.getElementById("app");
  if (!app || !pageConfig[page]) {
    return;
  }

  renderShell(page);

  function renderShell(currentPage) {
    const config = pageConfig[currentPage];
    app.innerHTML = `
      <div class="site-shell">
        <aside class="sidebar">
          <div class="brand-label">CodeLite</div>
          <h1 class="brand-title">Interview OS</h1>
          <p class="brand-copy">${escapeHtml(knowledge.projectMeta.tagline)}</p>
          <div class="sidebar-block">
            <h2 class="sidebar-heading">Pages</h2>
            <nav class="nav-list">
              ${Object.entries(pageConfig).map(([id, item]) => navLink(id, item, currentPage)).join("")}
            </nav>
          </div>
          <div class="sidebar-block">
            <h2 class="sidebar-heading">Must Say</h2>
            <div class="sidebar-mini-list">
              ${knowledge.mustKnow.slice(0, 3).map((item) => sidebarMini("要点", item)).join("")}
            </div>
          </div>
          <div class="sidebar-block">
            <h2 class="sidebar-heading">Red Flags</h2>
            <div class="sidebar-mini-list">
              ${knowledge.redFlags.slice(0, 2).map((item) => sidebarMini("别夸大", item)).join("")}
            </div>
          </div>
        </aside>
        <main class="main">
          <section class="hero">
            <div class="page-kicker">${config.kicker}</div>
            <h1>${config.title}</h1>
            <p>${config.intro}</p>
          </section>
          <div id="page-content"></div>
          <section class="footer-card">
            <p>复习顺序建议：先过总览页，再过架构页和机制页，然后刷问答，最后用图谱页做闭环回忆。如果面试只剩 10 分钟，优先记住项目定位、build_runtime、task-worktree、安全护栏和 validate pipeline。</p>
          </section>
        </main>
      </div>
    `;

    const content = document.getElementById("page-content");
    if (!content) {
      return;
    }
    if (currentPage === "home") renderHome(content);
    if (currentPage === "architecture") renderArchitecture(content);
    if (currentPage === "mechanisms") renderMechanisms(content);
    if (currentPage === "interview") renderInterview(content);
    if (currentPage === "resume") renderResume(content);
    if (currentPage === "graph") renderGraph(content);
  }

  function renderHome(container) {
    container.innerHTML = `
      <section class="section-card">
        <div class="section-header">
          <div>
            <h2>项目定位</h2>
            <p>${escapeHtml(knowledge.projectMeta.elevatorPitch)}</p>
          </div>
        </div>
        <div class="chip-row">
          ${knowledge.projectMeta.positioning.map((item) => `<span class="chip"><strong>定位</strong>${escapeHtml(item)}</span>`).join("")}
        </div>
      </section>

      <section class="section-card">
        <div class="section-header">
          <div>
            <h2>量化感</h2>
            <p>这些数字不是为了堆简历，而是为了让你在介绍项目时有工程体量和证据感。</p>
          </div>
        </div>
        <div class="metric-grid">
          ${knowledge.projectMeta.stats.map((item) => `
            <article class="metric-card">
              <div class="metric-label">${escapeHtml(item.label)}</div>
              <div class="metric-value">${escapeHtml(item.value)}</div>
              <div class="metric-note">${escapeHtml(item.note)}</div>
            </article>`).join("")}
        </div>
      </section>

      <section class="section-card">
        <div class="section-header">
          <div>
            <h2>六个亮点</h2>
            <p>首页最好背熟这六点，它们决定了面试官是否愿意继续深挖。</p>
          </div>
        </div>
        <div class="card-grid">
          ${knowledge.highlights.map((item) => `
            <article class="highlight-card">
              <h3>${escapeHtml(item.title)}</h3>
              <p>${escapeHtml(item.summary)}</p>
            </article>`).join("")}
        </div>
      </section>

      <section class="section-card">
        <div class="section-header">
          <div>
            <h2>演进时间线</h2>
            <p>用阶段来讲项目，比用文件列表更有说服力，也更符合面试交流节奏。</p>
          </div>
        </div>
        <div class="timeline">
          ${knowledge.timeline.map((item) => `
            <article class="timeline-item">
              <div class="timeline-stage">${escapeHtml(item.stage)}</div>
              <h3>${escapeHtml(item.title)}</h3>
              <p>${escapeHtml(item.summary)}</p>
              <ul class="link-list">${item.evidence.map((evidence) => linkItem(evidence.label, evidence.path)).join("")}</ul>
            </article>`).join("")}
        </div>
      </section>

      <section class="section-card">
        <div class="section-header">
          <div>
            <h2>面试前十分钟</h2>
            <p>这两组清单是压缩版的临场记忆锚点。</p>
          </div>
        </div>
        <div class="two-col">
          <article class="summary-card">
            <h3>一定要讲出来</h3>
            <ul class="bullet-list">${knowledge.mustKnow.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>
          </article>
          <article class="summary-card">
            <h3>一定不要夸大</h3>
            <ul class="bullet-list">${knowledge.redFlags.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>
          </article>
        </div>
      </section>

      <section class="section-card">
        <div class="section-header">
          <div>
            <h2>证据入口</h2>
            <p>被深挖时，尽量把回答落回代码、测试和 acceptance bundle，而不是停留在口头描述。</p>
          </div>
        </div>
        <div class="stack">
          ${knowledge.evidenceBundles.map((item) => `
            <article class="evidence-card">
              <h3>${escapeHtml(item.title)}</h3>
              <p>${escapeHtml(item.why)}</p>
              <ul class="link-list">${linkItem(item.path, item.path)}</ul>
            </article>`).join("")}
        </div>
      </section>
    `;
  }

  function renderArchitecture(container) {
    container.innerHTML = `
      <section class="section-card">
        <div class="section-header">
          <div>
            <h2>分层视图</h2>
            <p>按这八层去讲，面试官更容易理解你是从系统维度思考，而不是按文件顺序背代码。</p>
          </div>
        </div>
        <div class="stack">
          ${knowledge.architecture.layers.map((layer) => `
            <article class="mechanism-card">
              <div class="mechanism-meta"><span class="badge">${escapeHtml(layer.name)}</span></div>
              <h3>${escapeHtml(layer.headline)}</h3>
              <p>${escapeHtml(layer.interviewLine)}</p>
              <ul class="link-list" style="margin-top:14px;">${layer.modules.map((item) => moduleItem(item)).join("")}</ul>
            </article>`).join("")}
        </div>
      </section>

      <section class="section-card">
        <div class="section-header">
          <div>
            <h2>关键调用链</h2>
            <p>这部分建议用“输入 -> 决策 -> 执行 -> 落盘 -> 验证”的顺序回答。</p>
          </div>
        </div>
        <div class="stack">
          ${knowledge.architecture.flows.map((flow) => `
            <article class="flow-card">
              <h3>${escapeHtml(flow.title)}</h3>
              <ol class="ordered-list" style="margin-top:14px;">${flow.steps.map((step) => `<li>${escapeHtml(step)}</li>`).join("")}</ol>
              <p style="margin-top:14px;"><strong>面试价值：</strong>${escapeHtml(flow.whyItMatters)}</p>
              <ul class="link-list" style="margin-top:14px;">${flow.evidence.map((item) => linkItem(item.label, item.path)).join("")}</ul>
            </article>`).join("")}
        </div>
      </section>

      <section class="section-card">
        <div class="section-header">
          <div>
            <h2>设计选择与权衡</h2>
            <p>这里最适合回答“为什么这样做而不是那样做”的问题。</p>
          </div>
        </div>
        <div class="card-grid">
          ${knowledge.architecture.designChoices.map((item) => `
            <article class="highlight-card">
              <h3>${escapeHtml(item.choice)}</h3>
              <p><strong>为什么：</strong>${escapeHtml(item.why)}</p>
              <p style="margin-top:10px;"><strong>代价：</strong>${escapeHtml(item.tradeoff)}</p>
            </article>`).join("")}
        </div>
      </section>
    `;
  }

  function renderMechanisms(container) {
    const groups = ["全部", ...new Set(knowledge.mechanisms.map((item) => item.group))];
    container.innerHTML = `
      <section class="section-card">
        <div class="section-header">
          <div>
            <h2>机制索引</h2>
            <p>筛选后逐张卡练习。每张卡只记住问题、设计、代码位置、测试证据和面试说法。</p>
          </div>
        </div>
        <div class="mechanism-toolbar">
          <div class="filter-pills" id="mechanism-groups">
            ${groups.map((group, index) => `<button class="filter-pill ${index === 0 ? "is-active" : ""}" type="button" data-group="${escapeHtml(group)}">${escapeHtml(group)}</button>`).join("")}
          </div>
        </div>
        <div class="mechanism-list" id="mechanism-list"></div>
      </section>
    `;
    const buttons = Array.from(container.querySelectorAll("[data-group]"));
    const list = container.querySelector("#mechanism-list");
    let currentGroup = "全部";

    const paint = () => {
      if (!list) return;
      list.innerHTML = knowledge.mechanisms
        .filter((item) => currentGroup === "全部" || item.group === currentGroup)
        .map((item) => `
          <article class="mechanism-card">
            <div class="mechanism-meta">
              <span class="badge">${escapeHtml(item.group)}</span>
              <span class="badge is-secondary">${escapeHtml(item.id)}</span>
            </div>
            <h3>${escapeHtml(item.title)}</h3>
            <p><strong>问题：</strong>${escapeHtml(item.problem)}</p>
            <p style="margin-top:10px;"><strong>设计：</strong>${escapeHtml(item.design)}</p>
            <div class="two-col" style="margin-top:16px;">
              <div>
                <h3>实现抓手</h3>
                <ul class="bullet-list">${item.implementation.map((line) => `<li>${escapeHtml(line)}</li>`).join("")}</ul>
              </div>
              <div>
                <h3>面试答法</h3>
                <ul class="bullet-list">${item.interviewAngles.map((line) => `<li>${escapeHtml(line)}</li>`).join("")}</ul>
              </div>
            </div>
            <div class="two-col" style="margin-top:16px;">
              <div>
                <h3>证据</h3>
                <ul class="link-list">${item.evidence.map((line) => linkItem(line.label, line.path)).join("")}</ul>
              </div>
              <div>
                <h3>测试</h3>
                <ul class="link-list">${item.tests.map((line) => linkItem(line, line)).join("")}</ul>
              </div>
            </div>
          </article>`).join("");
    };

    buttons.forEach((button) => {
      button.addEventListener("click", () => {
        currentGroup = button.dataset.group || "全部";
        buttons.forEach((item) => item.classList.toggle("is-active", item === button));
        paint();
      });
    });
    paint();
  }

  function renderInterview(container) {
    const categories = ["全部", ...new Set(knowledge.interview.questions.map((item) => item.category))];
    container.innerHTML = `
      <section class="section-card">
        <div class="section-header">
          <div>
            <h2>答题策略</h2>
            <p>先拿下主叙事，再切到证据；回答里尽量带类名、命令名、测试文件或验收包。</p>
          </div>
        </div>
        <div class="two-col">
          <article class="summary-card">
            <h3>默认节奏</h3>
            <ul class="bullet-list">${knowledge.interview.strategy.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>
          </article>
          <article class="quote-card">
            <p>高分回答模板：先给结论，再给设计原因，再给一段代码、测试或验收证据，最后主动说边界。</p>
          </article>
        </div>
      </section>
      <section class="section-card">
        <div class="section-header">
          <div>
            <h2>高频问题库</h2>
            <p>建议先按类别刷，再用搜索框练“看题即答”。</p>
          </div>
        </div>
        <div class="qa-toolbar">
          <div class="filter-pills" id="qa-categories">
            ${categories.map((category, index) => `<button class="filter-pill ${index === 0 ? "is-active" : ""}" type="button" data-category="${escapeHtml(category)}">${escapeHtml(category)}</button>`).join("")}
          </div>
          <input id="qa-search" class="search-input" type="search" placeholder="搜索问题关键词，例如 worktree / validate / 安全" />
        </div>
        <div class="qa-list" id="qa-list"></div>
      </section>
    `;
    const buttons = Array.from(container.querySelectorAll("[data-category]"));
    const search = container.querySelector("#qa-search");
    const list = container.querySelector("#qa-list");
    let currentCategory = "全部";

    const paint = () => {
      if (!list) return;
      const keyword = String(search?.value || "").trim().toLowerCase();
      list.innerHTML = knowledge.interview.questions
        .filter((item) => currentCategory === "全部" || item.category === currentCategory)
        .filter((item) => !keyword || JSON.stringify(item).toLowerCase().includes(keyword))
        .map((item) => `
          <article class="qa-card">
            <details>
              <summary>
                <span>${escapeHtml(item.question)}</span>
                <span class="answer-chip">${escapeHtml(item.category)}</span>
              </summary>
              <div class="qa-answer-block">
                <div><div class="answer-chip">30 秒答法</div><p style="margin-top:8px;">${escapeHtml(item.answer30)}</p></div>
                <div><div class="answer-chip">2 分钟展开</div><p style="margin-top:8px;">${escapeHtml(item.answer120)}</p></div>
                <div><div class="answer-chip">继续追问</div><ul class="bullet-list">${item.followups.map((line) => `<li>${escapeHtml(line)}</li>`).join("")}</ul></div>
                <div><div class="answer-chip">建议回链的机制</div><div class="chip-row" style="margin-top:8px;">${item.related.map((line) => `<span class="chip"><strong>link</strong>${escapeHtml(line)}</span>`).join("")}</div></div>
              </div>
            </details>
          </article>`).join("");
    };

    buttons.forEach((button) => {
      button.addEventListener("click", () => {
        currentCategory = button.dataset.category || "全部";
        buttons.forEach((item) => item.classList.toggle("is-active", item === button));
        paint();
      });
    });
    search?.addEventListener("input", paint);
    paint();
  }

  function renderResume(container) {
    container.innerHTML = `
      <section class="section-card">
        <div class="section-header">
          <div>
            <h2>简历条目</h2>
            <p>推荐精简版放简历，增强版放项目介绍或自我介绍里展开。</p>
          </div>
        </div>
        <div class="stack">
          ${knowledge.resume.bullets.map((item) => `<article class="resume-card"><div class="badge">${escapeHtml(item.style)}</div><p style="margin-top:14px;">${escapeHtml(item.text)}</p></article>`).join("")}
        </div>
      </section>
      <section class="section-card">
        <div class="section-header">
          <div>
            <h2>STAR 拆法</h2>
            <p>被问“你做了什么、为什么这样做、结果是什么”时，直接按这四段说。</p>
          </div>
        </div>
        <div class="two-col">
          <article class="summary-card"><h3>Situation</h3><p>${escapeHtml(knowledge.resume.star.situation)}</p></article>
          <article class="summary-card"><h3>Task</h3><p>${escapeHtml(knowledge.resume.star.task)}</p></article>
          <article class="summary-card"><h3>Action</h3><p>${escapeHtml(knowledge.resume.star.action)}</p></article>
          <article class="summary-card"><h3>Result</h3><p>${escapeHtml(knowledge.resume.star.result)}</p></article>
        </div>
      </section>
      <section class="section-card">
        <div class="section-header">
          <div>
            <h2>讲稿脚本</h2>
            <p>先背 3 分钟版，再拿 5 分钟版做结构化展开，最后用 10 分钟版应对深挖面试。</p>
          </div>
        </div>
        <div class="script-list">
          ${knowledge.resume.scripts.map((item) => `<article class="script-card"><div class="script-length">${escapeHtml(item.length)}</div>${item.paragraphs.map((paragraph) => `<p>${escapeHtml(paragraph)}</p>`).join("")}</article>`).join("")}
        </div>
      </section>
      <section class="section-card">
        <div class="section-header">
          <div>
            <h2>关键词与禁区</h2>
            <p>关键词用来提高表达密度，禁区用来防止面试里把项目吹穿。</p>
          </div>
        </div>
        <div class="two-col">
          <article class="summary-card"><h3>建议高频出现</h3><div class="chip-row">${knowledge.resume.keywords.map((item) => `<span class="chip"><strong>key</strong>${escapeHtml(item)}</span>`).join("")}</div></article>
          <article class="summary-card"><h3>不要这样说</h3><ul class="bullet-list">${knowledge.resume.doNotSay.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul></article>
        </div>
      </section>
    `;
  }

  function renderGraph(container) {
    container.innerHTML = `
      <section class="section-card">
        <div class="section-header">
          <div>
            <h2>图谱用法</h2>
            <p>点击节点后，先回答“它做什么”，再回答“它依赖谁、被谁证明”。这比死记文件名更容易形成系统记忆。</p>
          </div>
        </div>
        <div class="graph-toolbar">
          <select id="graph-filter" class="graph-select">
            <option value="all">全部类型</option>
            ${Array.from(new Set(knowledge.graph.nodes.map((node) => node.type))).map((type) => `<option value="${escapeHtml(type)}">${escapeHtml(type)}</option>`).join("")}
          </select>
        </div>
        <div class="graph-shell">
          <div class="graph-canvas-wrap">
            <div class="graph-legend">
              ${Object.entries(typePalette).map(([type, color]) => `<span class="legend-chip"><span class="legend-dot" style="background:${color};"></span>${escapeHtml(type)}</span>`).join("")}
            </div>
            <svg id="graph-canvas" class="graph-canvas" viewBox="0 0 980 720" preserveAspectRatio="xMidYMid meet"></svg>
          </div>
          <aside class="graph-sidebar">
            <article class="graph-detail-card">
              <h3 id="graph-detail-title">点击任意节点</h3>
              <p id="graph-detail-copy">默认建议先点 CLI、AgentLoop、TaskRunner、ValidatePipeline，这四个节点足够串出项目主线。</p>
              <ul class="link-list" id="graph-detail-links" style="margin-top:14px;"></ul>
            </article>
            <article class="graph-detail-card">
              <h3>图谱记忆口诀</h3>
              <ul class="bullet-list">
                <li>入口层负责接住用户输入。</li>
                <li>核心层负责跑 turn 和工具。</li>
                <li>安全层负责限制能做什么。</li>
                <li>隔离层负责不同任务不互踩。</li>
                <li>治理层负责长时间运行不失控。</li>
                <li>质量和证据层负责证明已经完成。</li>
              </ul>
            </article>
          </aside>
        </div>
      </section>
    `;
    setupGraph(container);
  }

  function setupGraph(container) {
    const svg = container.querySelector("#graph-canvas");
    const select = container.querySelector("#graph-filter");
    const detailTitle = container.querySelector("#graph-detail-title");
    const detailCopy = container.querySelector("#graph-detail-copy");
    const detailLinks = container.querySelector("#graph-detail-links");
    if (!svg || !select || !detailTitle || !detailCopy || !detailLinks) return;

    const typeOrder = ["entry", "core", "safety", "isolation", "governance", "dispatch", "intelligence", "extension", "quality", "evidence", "presentation"];
    const columns = typeOrder.filter((type) => knowledge.graph.nodes.some((node) => node.type === type));
    const positions = {};
    columns.forEach((type, columnIndex) => {
      const nodes = knowledge.graph.nodes.filter((node) => node.type === type);
      nodes.forEach((node, rowIndex) => {
        const x = 100 + columnIndex * (780 / Math.max(columns.length - 1, 1));
        const y = 90 + rowIndex * (560 / Math.max(nodes.length - 1, 1));
        positions[node.id] = { x, y };
      });
    });

    let selectedId = null;
    let currentFilter = "all";

    const paint = () => {
      const filteredNodes = knowledge.graph.nodes.filter((node) => currentFilter === "all" || node.type === currentFilter);
      const filteredIds = new Set(filteredNodes.map((node) => node.id));
      const neighbors = new Set();
      knowledge.graph.edges.forEach((edge) => {
        if (edge.from === selectedId) neighbors.add(edge.to);
        if (edge.to === selectedId) neighbors.add(edge.from);
      });
      const edges = knowledge.graph.edges
        .filter((edge) => filteredIds.has(edge.from) && filteredIds.has(edge.to))
        .map((edge) => {
          const from = positions[edge.from];
          const to = positions[edge.to];
          const highlighted = selectedId && (edge.from === selectedId || edge.to === selectedId);
          return `<g><line class="graph-edge ${highlighted ? "is-highlighted" : ""}" x1="${from.x}" y1="${from.y}" x2="${to.x}" y2="${to.y}"></line><text x="${(from.x + to.x) / 2}" y="${(from.y + to.y) / 2 - 8}" text-anchor="middle" class="graph-node-type">${escapeHtml(edge.label)}</text></g>`;
        })
        .join("");
      const nodes = filteredNodes
        .map((node) => {
          const pos = positions[node.id];
          const selected = node.id === selectedId;
          const dimmed = selectedId && !selected && !neighbors.has(node.id);
          const color = typePalette[node.type] || "#4c4c4c";
          return `<g class="graph-node ${selected ? "is-selected" : ""} ${dimmed ? "is-dimmed" : ""}" data-node-id="${escapeHtml(node.id)}" transform="translate(${pos.x}, ${pos.y})"><rect class="graph-node-shape" x="-62" y="-26" width="124" height="52" rx="16"></rect><circle cx="-48" cy="0" r="7" fill="${color}"></circle><text x="-32" y="-4">${escapeHtml(node.label)}</text><text x="-32" y="14" class="graph-node-type">${escapeHtml(node.type)}</text></g>`;
        })
        .join("");
      svg.innerHTML = edges + nodes;
      Array.from(svg.querySelectorAll("[data-node-id]")).forEach((element) => {
        element.addEventListener("click", () => {
          selectedId = element.getAttribute("data-node-id");
          const node = knowledge.graph.nodes.find((item) => item.id === selectedId);
          if (!node) return;
          detailTitle.textContent = node.label;
          detailCopy.textContent = node.description;
          detailLinks.innerHTML = node.refs.map((ref) => linkItem(ref, ref)).join("");
          paint();
        });
      });
    };

    select.addEventListener("change", () => {
      currentFilter = select.value;
      if (currentFilter !== "all" && selectedId) {
        const selected = knowledge.graph.nodes.find((node) => node.id === selectedId);
        if (selected && selected.type !== currentFilter) {
          selectedId = null;
          detailTitle.textContent = "点击任意节点";
          detailCopy.textContent = "默认建议先点 CLI、AgentLoop、TaskRunner、ValidatePipeline，这四个节点足够串出项目主线。";
          detailLinks.innerHTML = "";
        }
      }
      paint();
    });
    paint();
  }

  function navLink(id, item, currentPage) {
    const href = id === "home" ? "index.html" : `${id}.html`;
    const active = id === currentPage ? "is-active" : "";
    return `<a class="nav-link ${active}" href="${href}"><span class="nav-name">${item.label}</span><span class="nav-note">${item.note}</span></a>`;
  }

  function sidebarMini(title, body) {
    return `<div class="sidebar-mini-item"><strong>${escapeHtml(title)}</strong><span>${escapeHtml(body)}</span></div>`;
  }

  function linkItem(label, path) {
    return `<li><a href="../../${path}">${escapeHtml(label)}</a></li>`;
  }

  function moduleItem(text) {
    const path = text.includes("/") ? text : "codelite/cli.py";
    return linkItem(text, path);
  }

  function escapeHtml(text) {
    return String(text)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }
})();
