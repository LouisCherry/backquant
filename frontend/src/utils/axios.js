import axios from 'axios';

// 创建axios实例
const instance = axios.create();

// 请求拦截器
instance.interceptors.request.use(
  (config) => {
    // 从localStorage获取token
    const token = localStorage.getItem('token');
    if (token) {
      config.headers.Authorization = `Bearer ${token}`;
    }
    return config;
  },
  (error) => {
    return Promise.reject(error);
  }
);

// 响应拦截器
instance.interceptors.response.use(
  (response) => {
    return response;
  },
  (error) => {
    // 如果返回401未授权，清除token并跳转到登录页面
    if (error.response && error.response.status === 401) {
      localStorage.removeItem('token');
      localStorage.removeItem('userid');
      // 跳转到登录页面
      if (window.location) {
        window.location.reload(); // 刷新页面回到登录状态
      }
    }
    return Promise.reject(error);
  }
);

export default instance; 