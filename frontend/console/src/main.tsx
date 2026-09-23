import React from "react";
import ReactDOM from "react-dom/client";
import { ConsoleApp } from "./App";
import "../../shared/styles.css";
ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ConsoleApp />
  </React.StrictMode>,
);
