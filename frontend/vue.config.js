const config = require('./config');

module.exports = {
  devServer: {
    port: 8081,
    proxy: {
      '/jupyter/lab/api': {
        target: 'http://localhost:8889',
        changeOrigin: true,
        secure: false
      },
      '/jupyter/api': {
        target: 'http://localhost:8889',
        changeOrigin: true,
        secure: false
      },
      '/jupyter': {
        target: 'http://localhost:8889',
        changeOrigin: true,
        secure: false,
        ws: true,
        headers: {
          'Origin': 'http://localhost:8889'
        }
      },
      '/api': {
        target: config.API_SERVER,
        changeOrigin: true,
        secure: false
      }
    }
  }
}
