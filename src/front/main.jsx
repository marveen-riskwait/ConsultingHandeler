import React from 'react'
import ReactDOM from 'react-dom/client'
import './index.css'  // Global styles for your application
import { RouterProvider } from "react-router-dom";  // Import RouterProvider to use the router
import { router } from "./routes";  // Import the router configuration
import { StoreProvider } from './hooks/useGlobalReducer';  // Import the StoreProvider for global state management

const Main = () => {
    // Same-origin now (Vite proxy in dev, Flask-served bundle in prod), so the
    // old "set VITE_BACKEND_URL" setup screen no longer applies.
    return (
        <React.StrictMode>  
            {/* Provide global state to all components */}
            <StoreProvider> 
                {/* Set up routing for the application. The future flag opts in
                    to React Router v7's startTransition behavior (and silences
                    the deprecation warning). */}
                <RouterProvider router={router}
                    future={{ v7_startTransition: true }} />
            </StoreProvider>
        </React.StrictMode>
    );
}

// Render the Main component into the root DOM element.
ReactDOM.createRoot(document.getElementById('root')).render(<Main />)
