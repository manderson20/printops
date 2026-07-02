"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useToken } from "@/lib/auth";

export default function Home() {
  const router = useRouter();
  const token = useToken();

  useEffect(() => {
    router.replace(token ? "/printers" : "/login");
  }, [router, token]);

  return null;
}
