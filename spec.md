Potřeboval bych vytvořit aplikaci na odesílání sms pomocí react-native a modulu na propojení odoo 18 kampaní 

Jak se handlují SMS přes react native a custom plugin na SEND_SMS
/Volumes/ext-msi/projects/sidonio-pos/apps/branch-app-rn

jak se propojí sms s mass mailing kampaněmi, přidej mi tam i pausování kampaně (mailing.mailing paused) 
@varyshop/sms_httpsms 
Přidej mi tam i bulk send
@varyshop/sms_send_bulk

Propojení systému a aplikace se bude provádět přes QR (stejně jako v branch-app-rn) modul se kterým komunikuje branch app najdeš zde
/Volumes/ext-msi/projects/sidonio-pos/sidonio_varyshop/varyshop/pos_incoming_call

Potřeboval bych aby modul nahradil odoo iap pro posílání sms, stejně jako to dělá sms_httpsms

Modul by měl podporovat napojení na více čísel najednou a bude automaticky rozdělovat load na čísla která mu posílají heartbeat. Každé číslo musí mít počítadlo kolik sms se odeslalo, každé číslo má vlastní limit sms které může odeslat a tak se musí trackovat přesný počet aby se nepřiřazovaly sms číslu který už dosáhl maxima.

Model na přiřazení tel. čísla by měl také podporovat filtr aby se na tel. číslo přiřazovaly pouze záznamy vybrané domény. Např. pouze pokud záznam res.partners má v category_ids 10  ["categ_ids", "in", 10]
Model spravuje frontu - u tel. čísla se musí hlídat timeout mezi sms (zvolí se např 100 sms za minutu)  

Aplikace by měla dotazovat aktuální stav fronty pro čísla připojená k telefonu (telefon může mít až 2 čísla). 

Aplikace bude na webhook endpoint posílat eventy se stavy odeslání sms - odesílám/error/odesláno (podobně jako to dělá httpsms)

Aplikace by měla číst zprávy a pokud je ve zprávě STOP, číslo se dá na blacklist (momentálně je v sms odkaz - zabírá moc místa, předělej na "STOP pro odhlaseni" (nepoužívej pro tuto zprávu diakritiku)


Připravil jsem složku na moduly a aplikaci v extra/sms


Také bych do sms_modules potřeboval přidat modul ve kterém bude kompletní dokumentace jak nainstalovat aplikaci, jak nastavit modul, jak nastavit kampaň, jak nastavit filtrování domény pro kampaně, jak spustit odeslání, jak fungují odkazy, odhlášení (blacklist - GDPR), jak se trackuje výkon z SMS pomocí  link tracker odkazu a vše co tě ještě napadně

Mělo by být možné nastavit i monthly limit, pokud hitne monthly, musí počkat do dalšího období. Také by se měl dát nastavit začátek období aby se vědělo kdy resetovat

Máme modul sms_send_bulk ve kterém je akce "Send Now" Podobně bych chtěl udělat akci "Send Now with" a ukáže se mi modal ve kterém bude seznam čísel sms_gateway kde vyberu (multiselect) tel. čísla které mají zprávy odeslat. Jde o bulk akci a tak mi systém vybrané sms přerozdělí pro optimalizaci loadu. Také musí kontrolovat stavy limitů a přiřadit sms pouze do max. limitu. 

Ujisti se že se stavy limitů sms aktualizují podle velikosti zprávy - 1 sms není vždy 1 sms. Větší zprávy a sms se speciálními znaky zabírají více místa (např. 3 sms) a tak se musí odečíst podle opravdové velikosti, aby limity seděli s tarifem operátora.

sms.sms by mělo mít na výběr která SIM patří k SMS, SIM1 může patřit jiné společnosti než SIM2 a tak musí být možné vybrat specifickou SIM

Také potřebujeme mít v rámci sms_gateway modulu upravané všechny views spojené se sms ať jde vidět komu sms patří (sms.sms_tsms_view_form)