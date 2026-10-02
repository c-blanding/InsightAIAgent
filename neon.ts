import { defineConfig } from "@neon/config/v1";

export default defineConfig({
  auth: true,
  preview: {
    buckets: {
      "insight-screenshots": { access: "private" },
      "insight-videos": { access: "private" },
    },
  },
});
