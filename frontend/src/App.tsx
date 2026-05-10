import {useEffect} from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import LoginPage from './pages/LoginPage';
import ChatPage from './pages/ChatPage';
import HomePage from './pages/HomePage';
import AppWorkspacePage from './pages/AppWorkspacePage';
import DocumentPage from './pages/context/DocumentPage';
import SkillFilesPage from './pages/context/SkillFilesPage';
import {useUIStore} from './stores/useUIStore';

// Create QueryClient instance
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      retry: 1,
        staleTime: 5 * 60 * 1000, // 5 minutes
    },
  },
});

function App() {
    const {theme, setTheme} = useUIStore();

    // Initialize theme on mount
    useEffect(() => {
        setTheme(theme);
    }, [setTheme, theme]);

  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
            <Route path="/home" element={<HomePage/>}/>
            <Route path="/triggers" element={<HomePage defaultTab="trigger" />}/>
            <Route path="/app" element={<AppWorkspacePage />} />
          <Route path="/chat" element={<ChatPage />} />
            <Route path="/knowledge/:knowledgeId/documents" element={<DocumentPage/>}/>
            <Route path="/skills/:skillId/files" element={<SkillFilesPage/>}/>
            <Route path="/" element={<Navigate to="/app" replace/>}/>
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  );
}

export default App;
