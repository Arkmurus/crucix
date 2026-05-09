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

    // R-F51: watchlist alerts bell
    this._bindAlerts();
  },

  // R-F51: watchlist alerts bell + popup
  _bindAlerts() {
    const bell = document.getElementById('alerts-bell');
    const popup = document.getElementById('alerts-popup');
    const badge = document.getElementById('alerts-badge');
    const list = document.getElementById('alerts-list');
    const markBtn = document.getElementById('alerts-mark-read');
    if (!bell || !popup || !list || !badge) return;

    const userId = (() => {
      try {
        const u = JSON.parse(localStorage.getItem('crucix_user') || '{}');
        return (u.id || u.username || '').toString().replace(/[^A-Za-z0-9]/g, '');
      } catch { return ''; }
    })();

    const authedFetch = (url, opts = {}) => {
      const token = localStorage.getItem('crucix_token') || '';
      return fetch(url, {
        ...opts,
        headers: {
          ...(opts.headers || {}),
          'Authorization': 'Bearer ' + token,
          ...(opts.body ? { 'Content-Type': 'application/json' } : {}),
        },
      });
    };

    const refreshBadge = async () => {
      if (!userId) return;
      try {
        const resp = await authedFetch(
          '/api/aria/dd/watchlist/alerts/unread-count?user_id=' + encodeURIComponent(userId),
        );
        if (!resp.ok) return;
        const data = await resp.json();
        const n = parseInt(data.unread_count, 10) || 0;
        if (n > 0) {
          badge.textContent = n > 99 ? '99+' : String(n);
          badge.style.display = '';
        } else {
          badge.style.display = 'none';
        }
      } catch {}
    };

    const renderAlerts = (alerts) => {
      list.innerHTML = '';
      if (!alerts.length) {
        list.innerHTML = '<div style="padding:24px;text-align:center;color:rgba(255,255,255,0.3);font-size:13px"><i class="bi bi-check-circle" style="font-size:18px;margin-bottom:6px;display:block"></i>No watchlist alerts in the last 7 days.</div>';
        return;
      }
      alerts.forEach(a => {
        const sev = (a.change_type || '').toLowerCase();
        const sevColor = (sev === 'new_hit' || sev === 'new_pep') ? '#ef4444' :
                         sev === 'score_change' ? '#f59e0b' :
                         sev === 'removed' ? '#22c55e' : '#7c3aed';
        const ts = a.timestamp ? new Date(a.timestamp).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '';
        const item = document.createElement('div');
        item.style.cssText = 'padding:12px 16px;border-bottom:1px solid rgba(255,255,255,0.04);font-size:13px;' + (a.read ? 'opacity:0.55' : '');
        item.innerHTML =
          '<div style="display:flex;align-items:flex-start;gap:10px">' +
            '<div style="width:6px;height:6px;border-radius:50%;background:' + sevColor + ';flex-shrink:0;margin-top:6px"></div>' +
            '<div style="flex:1;min-width:0">' +
              '<div style="font-weight:600;color:#fff;line-height:1.35;margin-bottom:2px">' + escapeText(a.entity || '(unknown)') + '</div>' +
              '<div style="font-size:12px;color:rgba(255,255,255,0.6);line-height:1.4">' +
                escapeText((a.change_type || '').replace(/_/g, ' ')) +
                (a.old_status && a.new_status && a.old_status !== a.new_status
                  ? ': ' + escapeText(a.old_status) + ' → ' + escapeText(a.new_status)
                  : '') +
              '</div>' +
              (a.impacted_deals && a.impacted_deals.length
                ? '<div style="font-size:11px;color:#fbbf24;margin-top:3px"><i class="bi bi-link-45deg"></i> ' + a.impacted_deals.length + ' deal' + (a.impacted_deals.length === 1 ? '' : 's') + ' impacted</div>'
                : '') +
              '<div style="font-size:11px;color:rgba(255,255,255,0.35);margin-top:4px">' + ts + '</div>' +
            '</div>' +
          '</div>';
        list.appendChild(item);
      });
    };

    const loadAlerts = async () => {
      list.innerHTML = '<div style="padding:24px;text-align:center;color:rgba(255,255,255,0.3);font-size:13px">Loading…</div>';
      try {
        const resp = await authedFetch(
          '/api/aria/dd/watchlist/alerts?since_hours=168&user_id=' + encodeURIComponent(userId),
        );
        if (!resp.ok) {
          list.innerHTML = '<div style="padding:24px;text-align:center;color:rgba(255,255,255,0.4);font-size:13px">Failed to load alerts (HTTP ' + resp.status + ').</div>';
          return;
        }
        const data = await resp.json();
        renderAlerts(data.alerts || []);
      } catch (err) {
        list.innerHTML = '<div style="padding:24px;text-align:center;color:rgba(255,255,255,0.4);font-size:13px">Network error: ' + escapeText(err.message) + '</div>';
      }
    };

    bell.addEventListener('click', e => {
      e.stopPropagation();
      const isOpen = popup.style.display !== 'none' && popup.style.display !== '';
      if (isOpen) {
        popup.style.display = 'none';
      } else {
        popup.style.display = 'flex';
        loadAlerts();
      }
    });

    document.addEventListener('click', e => {
      if (!e.target.closest('#alerts-popup') && !e.target.closest('#alerts-bell')) {
        popup.style.display = 'none';
      }
    });

    if (markBtn) {
      markBtn.addEventListener('click', async () => {
        if (!userId) return;
        markBtn.disabled = true;
        markBtn.textContent = 'Marking…';
        try {
          await authedFetch('/api/aria/dd/watchlist/alerts/read', {
            method: 'POST',
            body: JSON.stringify({ user_id: userId }),
          });
          badge.style.display = 'none';
          await loadAlerts();
        } catch {}
        markBtn.disabled = false;
        markBtn.textContent = 'Mark all read';
      });
    }

    function escapeText(s) {
      return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
      }[c]));
    }

    // Initial badge load + poll every 60s
    refreshBadge();
    setInterval(refreshBadge, 60000);
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
        ${link('brain',   '/aria-brain',   'bi-heart-pulse', 'ARIA Brain')}

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
        <!-- R-F51 watchlist alerts bell -->
        <div style="position:relative">
          <button class="header-icon-btn" id="alerts-bell" title="Watchlist alerts">
            <i class="bi bi-bell"></i>
            <span id="alerts-badge" style="display:none;position:absolute;top:-2px;right:-2px;min-width:16px;height:16px;border-radius:8px;background:#ef4444;color:#fff;font-size:10px;font-weight:700;line-height:16px;padding:0 4px;text-align:center;box-shadow:0 0 0 2px #0c0919"></span>
          </button>
          <div id="alerts-popup" style="display:none;position:absolute;top:calc(100% + 8px);right:0;width:380px;max-width:calc(100vw - 24px);max-height:70vh;background:#1a1430;border:1px solid rgba(255,255,255,0.08);border-radius:12px;box-shadow:0 12px 40px rgba(0,0,0,0.5);z-index:100;overflow:hidden;display:flex;flex-direction:column">
            <div style="display:flex;align-items:center;justify-content:space-between;padding:14px 16px;border-bottom:1px solid rgba(255,255,255,0.06);flex-shrink:0">
              <div style="font-size:13px;font-weight:700;color:#fff;letter-spacing:0.3px;text-transform:uppercase">Watchlist alerts</div>
              <button id="alerts-mark-read" style="background:transparent;border:1px solid rgba(255,255,255,0.1);color:rgba(255,255,255,0.55);font-size:11px;padding:4px 10px;border-radius:6px;cursor:pointer">Mark all read</button>
            </div>
            <div id="alerts-list" style="flex:1;overflow-y:auto;padding:6px 0">
              <div style="padding:24px;text-align:center;color:rgba(255,255,255,0.3);font-size:13px">Loading…</div>
            </div>
          </div>
        </div>
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
            <a href="/account.html" class="nav-dropdown-item">
              <i class="bi bi-person-circle"></i> Account & billing
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
