/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE_URL?: string;
  readonly VITE_USE_MOCKS?: string;
  readonly VITE_MOCK_USER_ENDPOINTS?: string;
  readonly VITE_MOCK_ERROR_RATE?: string;
}
