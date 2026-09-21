import deckyPlugin from "@decky/rollup";

export default deckyPlugin({
  // 官方模板的封装已经处理好了 external / globals / iife 输出，
  // 以及把产物写到 dist/index.js。需要额外 Rollup 选项时加在这里。
});
