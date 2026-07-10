"use client";

import { LdapAddressBookCard } from "../LdapAddressBook";
import { usePrinterDetail } from "../PrinterDetailContext";
import { WebLoginCredentialsCard } from "../WebLoginCredentials";

export default function CredentialsTab() {
  const { printer, setPrinter } = usePrinterDetail();

  return (
    <div className="flex flex-col gap-6">
      <LdapAddressBookCard printer={printer} onUpdate={setPrinter} />
      <WebLoginCredentialsCard printer={printer} onUpdate={setPrinter} />
    </div>
  );
}
