"use client";

import React from "react";

const SmoothScroll = ({ children }: { children: React.ReactNode }) => {
  // Global smooth scrolling bypass: 
  // Returning native children to restore basic, default browser scroll behavior 
  // (removes scrolljacking / virtual physics)
  return <>{children}</>;
};

export default SmoothScroll;
