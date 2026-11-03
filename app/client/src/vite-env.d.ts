/// <reference types="vite/client" />

/** True in the static demo build (VITE_DEMO=static): the API runs in the browser. Set by vite.config.ts. */
declare const __STATIC_DEMO__: boolean;

interface ImportMetaEnv {
  readonly VITE_DEMO?: string;
  readonly VITE_API_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
