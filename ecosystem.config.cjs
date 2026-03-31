// PM2 process manager config — ensures 24/7 uptime on Seenode/VPS
// Usage:  pm2 start ecosystem.config.cjs
//         pm2 save && pm2 startup   (persist across server reboots)
module.exports = {
  apps: [
    {
      name:             'crucix',
      script:           'server.mjs',
      interpreter:      'node',
      interpreter_args: '--experimental-vm-modules',

      // Restart behaviour
      autorestart:      true,         // restart on crash
      restart_delay:    3000,         // wait 3s before restart
      max_restarts:     50,           // give up after 50 rapid crashes (prevents crash loop)
      min_uptime:       '10s',        // must stay up 10s to count as a successful start

      // Resource limits — prevent runaway memory (Node.js + OSINT sweep can grow)
      max_memory_restart: '512M',

      // Environment
      env: {
        NODE_ENV:    'production',
        PORT:        process.env.PORT || '3117',
        SELF_PING:   'false',         // not needed — PM2 keeps process alive
      },

      // Logging
      out_file:   './runs/logs/out.log',
      error_file: './runs/logs/error.log',
      log_date_format: 'YYYY-MM-DD HH:mm:ss',
      merge_logs:   true,

      // Graceful shutdown
      kill_timeout: 5000,
      listen_timeout: 10000,
    },
  ],
};
