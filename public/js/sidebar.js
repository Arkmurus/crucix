/**
 * Injects the shared sidebar + header into any dashboard page.
 * Call: Sidebar.init('page-key')
 */
const Sidebar = {
  init(activePage) {
    document.getElementById('sidebar-placeholder').innerHTML = this.html(activePage);
    document.getElementById('header-placeholder').innerHTML = this.headerHtml();
    this._bindEvents();
    Auth.me().then(user => {
      if (!user) return;
      const name = document.getElementById('nav-user-name');
      const av   = document.getElementById('nav-avatar');
      const role = document.getElementById('nav-role');
      if (name) name.textContent = user.fullName || user.username;
      if (av)   av.textContent = Auth.initials(user);
      if (role) role.textContent = user.role || 'analyst';
      // Show admin links
      if (user.role === 'admin') {
        document.querySelectorAll('[data-admin]').forEach(e => e.style.display = '');
      }
    });
  },

  _bindEvents() {
    // Sidebar toggle
    const toggle = document.getElementById('sidebar-toggle');
    const sidebar = document.getElementById('app-sidebar');
    const overlay = document.getElementById('sidebar-overlay');

    if (toggle && sidebar) {
      toggle.addEventListener('click', () => {
        if (window.innerWidth <= 1024) {
          sidebar.classList.toggle('mobile-open');
          overlay && overlay.classList.toggle('show');
        } else {
          sidebar.classList.toggle('collapsed');
        }
      });
    }

    if (overlay) {
      overlay.addEventListener('click', () => {
        sidebar.classList.remove('mobile-open');
        overlay.classList.remove('show');
      });
    }

    // User dropdown
    const navAvatar = document.getElementById('nav-avatar');
    const dropdown  = document.getElementById('nav-dropdown');
    if (navAvatar && dropdown) {
      navAvatar.addEventListener('click', e => {
        e.stopPropagation();
        dropdown.classList.toggle('open');
      });
      document.addEventListener('click', e => {
        if (!e.target.closest('#nav-dropdown') && !e.target.closest('#nav-avatar')) {
          dropdown.classList.remove('open');
        }
      });
    }

    // Logout
    const logoutBtn = document.getElementById('btn-logout');
    if (logoutBtn) logoutBtn.addEventListener('click', e => { e.preventDefault(); Auth.logout(); });
  },

  html(activePage) {
    const link = (page, href, icon, label, extra='') => {
      const cls = activePage === page ? ' active' : '';
      return `<a href="${href}" class="sidebar-link${cls} ${extra}" data-page="${page}">
        <i class="bi ${icon}"></i>${label}
      </a>`;
    };
    return `
    <div id="sidebar-overlay" class="sidebar-overlay"></div>
    <aside id="app-sidebar">
      <div class="sidebar-brand">
        <div class="sidebar-logo-mark"></div>
        <span class="sidebar-brand-name">ARKMURUS</span>
      </div>
      <nav class="sidebar-nav">
        <div class="sidebar-section">Intelligence</div>
        ${link('brief',       '/dashboard.html',         'bi-radar',          'Intelligence Brief')}
        ${link('opportunities','/opportunities.html',     'bi-briefcase',      'Opportunities')}
        ${link('bd',           '/bd-intelligence.html',   'bi-graph-up-arrow', 'BD Intelligence')}
        ${link('explorer',     '/explorer.html',          'bi-globe2',         'Explorer')}
        ${link('aria',         '/aria.html',              'bi-cpu',            'ARIA', 'aria-link')}

        <div class="sidebar-divider"></div>
        <div class="sidebar-section">System</div>
        ${link('sources', '/sources.html', 'bi-activity', 'Source Health')}

        <div data-admin style="display:none">
          <div class="sidebar-divider"></div>
          <div class="sidebar-section">Admin</div>
          ${link('admin', '/admin.html', 'bi-shield-lock', 'Users')}
        </div>
      </nav>
    </aside>`;
  },

  headerHtml() {
    return `
    <header id="app-header">
      <button class="header-toggle" id="sidebar-toggle" title="Toggle sidebar">
        <i class="bi bi-list"></i>
      </button>
      <span class="header-brand d-none d-md-block">ARKMURUS INTELLIGENCE</span>
      <div class="header-spacer"></div>
      <div class="header-actions">
        <a href="/sources.html" class="header-icon-btn" title="Source Health">
          <i class="bi bi-activity"></i>
        </a>
        <div class="nav-dropdown" style="position:relative">
          <div class="nav-avatar" id="nav-avatar" title="Profile">?</div>
          <div class="nav-dropdown-menu" id="nav-dropdown">
            <div class="nav-dropdown-user">
              <div class="nav-dropdown-name" id="nav-user-name">Loading…</div>
              <div class="nav-dropdown-role" id="nav-role">analyst</div>
            </div>
            <div class="nav-dropdown-divider"></div>
            <a href="/dashboard.html" class="nav-dropdown-item">
              <i class="bi bi-radar"></i> Intelligence Brief
            </a>
            <a href="/sources.html" class="nav-dropdown-item">
              <i class="bi bi-activity"></i> Source Health
            </a>
            <div class="nav-dropdown-divider"></div>
            <button class="nav-dropdown-item danger" id="btn-logout">
              <i class="bi bi-box-arrow-right"></i> Sign Out
            </button>
          </div>
        </div>
      </div>
    </header>`;
  }
};
