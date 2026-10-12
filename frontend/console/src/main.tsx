import React from "react";
import ReactDOM from "react-dom/client";
import { createBrowserRouter, RouterProvider } from "react-router-dom";
import { ConsoleApp } from "./App";
import "./Experience.css";
import "./CreationExperience.css";
const router=createBrowserRouter([{path:"*",element:<ConsoleApp/>}],{basename:"/console"});
ReactDOM.createRoot(document.getElementById("root")!).render(<React.StrictMode><RouterProvider router={router}/></React.StrictMode>);
