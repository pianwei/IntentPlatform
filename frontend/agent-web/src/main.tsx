import React from "react";
import ReactDOM from "react-dom/client";
import { AgentApp } from "./App";
import "./RuntimeBase.css";
ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <AgentApp />
  </React.StrictMode>,
);
