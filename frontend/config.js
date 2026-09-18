/* ==========================================================================
   Runtime configuration.

   API_BASE_URL is the origin of the backend service, with no trailing slash.
   Leave it empty ("") to call /api/... on the same origin as this page.

   This committed value is the LOCAL DEVELOPMENT default. On Render it is
   overwritten during the build by build.sh, which reads the API_BASE_URL
   environment variable — so editing this file does not affect a deploy.
   ========================================================================== */

window.APP_CONFIG = {
  API_BASE_URL: "http://localhost:8000"
};
