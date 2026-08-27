# Homeopathic Case-Taking & Clinical History Form

Source: `Homeopathic_Case_Taking_Form_Dr_Anwar_H_Biswas.docx`, supplied by
Global Homeopathy Clinic, 2026-08-28. This is a faithful transcription of the
paper form, kept beside the original so the digital version can be diffed
against what the doctor actually uses. **It is a source document, not a
specification** — the field list below is what the paper asks for, not what the
system should necessarily store. See the ADR for the mapping decisions.

Header on the paper: GLOBAL HOMEOPATHY CLINIC · Dr. Anwar H. Biswas ·
Homeopathic Physician.

---

## 1. Patient identification

| Field | Notes |
|---|---|
| Patient ID / File No. | |
| Date | dd / mm / yyyy |
| Time | |
| Patient Name | |
| Age | |
| Sex | |
| Date of Birth | dd / mm / yyyy |
| Marital Status | |
| Occupation | |
| Address | |
| Phone | |
| Email | |
| Emergency Contact | |
| Referred By | |

## 2. Chief complaints

Repeating table, five printed rows.

| Complaint / Site | Onset | Duration | Character / Sensation | Intensity |
|---|---|---|---|---|

## 3. History of present complaint

- First noticed on
- Sudden / Gradual
- Possible cause / exciting factor
- Progression
- Previous episodes
- Treatment already taken
- Response to treatment
- Associated symptoms
- **Chronology / narrative** — three ruled lines, free prose

## 4. Past medical & surgical history

- Childhood illnesses
- Major illnesses
- Hospitalizations
- Operations / surgeries
- Injuries / accidents
- Allergies / sensitivities
- Previous chronic treatment
- Other relevant history

## 5. Family history

- Father
- Mother
- Siblings
- Spouse / Children
- Diabetes / Hypertension
- Cancer / TB
- Mental / neurological illness
- Hereditary / constitutional tendencies

## 6. Personal history & habits

- Diet / appetite
- Food preferences / aversions
- Thirst
- Water intake
- Sleep
- Dreams
- Exercise / activity
- Tobacco / nicotine
- Alcohol / substance use
- Caffeine / tea / coffee
- Bowel habit
- Urination

## 7. Mental & emotional generals

- Temperament / disposition
- Anxiety / fears
- Anger / irritability
- Grief / disappointment
- Jealousy / suspicion
- Company / solitude
- Concentration / memory
- Work / responsibility response
- Relationships / social behavior
- Other striking mental symptoms
- **Important mental generals / exact expressions** — two ruled lines, free prose

## 8. Physical generals

- Thermal state — printed as `Hot / Chilly / Variable`, i.e. a closed choice
- Perspiration
- Appetite
- Thirst
- Cravings
- Aversions
- Food intolerances
- Sleep position / quality
- Energy / vitality
- Sensitivity to weather
- Menstrual / hormonal history (where applicable)
- Other physical generals

## 9. Modalities & concomitants

Fixed eight-row grid.

| Factor | Better | Worse | Notes / Concomitant |
|---|---|---|---|
| Time | | | |
| Position | | | |
| Motion / Rest | | | |
| Temperature | | | |
| Weather | | | |
| Food / Drink | | | |
| Pressure / Touch | | | |
| Other | | | |

## 10. System review

- General / constitutional
- Respiratory
- Cardiovascular
- Gastrointestinal
- Genitourinary
- Musculoskeletal
- Neurological
- Skin
- ENT / Eyes
- Endocrine

## 11. Clinical examination

- General appearance
- Pulse (/min)
- BP (mmHg)
- Temperature (°F / °C)
- Respiratory rate (/min)
- SpO₂ (%)
- Height
- Weight
- BMI
- Pallor / Cyanosis / Icterus / Edema
- Lymph nodes
- Other findings
- **Local / systemic examination findings** — two ruled lines, free prose

## 12. Investigations & reports

Repeating table, six printed rows.

| Date | Investigation | Result | Reference / Impression | Attachment / Report No. |
|---|---|---|---|---|

## 13. Clinical assessment

- Provisional diagnosis
- Differential diagnosis
- Miasmatic / constitutional assessment
- Totality of symptoms
- **Characteristic / peculiar symptoms** — two ruled lines, free prose

## 14. Repertorization / remedy analysis

Repeating table, six printed rows.

| Rubric / Symptom | Grade | Candidate Remedy | Score / Rank | Remarks |
|---|---|---|---|---|

## 15. Prescription

- Remedy
- Potency
- Dose
- Repetition
- Route / Vehicle
- Duration / Quantity
- Auxiliary advice
- Next follow-up (dd / mm / yyyy)
- **Prescription / clinical instructions** — two ruled lines, free prose

## 16. Follow-up record

Repeating table.

| Date | Remedy / Potency | Patient response | New symptoms | Old symptoms | Changes / Plan | Next date |
|---|---|---|---|---|---|---|

---

## Overlaps to resolve before building

These are properties of the paper form, written down here so the same
observation does not have to be rediscovered:

1. **§15 and §16 already exist in Clinicore.** §15 is `Prescription` +
   `PrescriptionItem` (remedy, strength/potency, dosage, frequency, duration,
   instructions, pack size, preparation) and `Encounter.follow_up_date`. §16 is
   the encounter timeline. Rebuilding either would create the second source of
   truth this project has rejected three times.
2. **§6 and §8 ask for appetite and thirst twice.** On paper that is harmless
   duplication in two different framings; in a form it is the same box asked
   twice, which is the kind of thing that gets one of them left blank.
3. **§4 overlaps `PatientClinicalProfile`**, which already holds
   `medical_history` and `allergies`.
4. **§1 is mostly `Patient` already** — name, DOB, sex, phone, address, code.
   Missing: marital status, occupation, email, emergency contact, referred by.
5. **§11 is per-visit, not per-patient.** Blood pressure, pulse and weight are
   observations at a moment. The paper form records them once because it is one
   sheet per patient; the system records visits, and a weight from three years
   ago printed on today's prescription is wrong.
6. **§8's thermal state is the one closed list on the whole form**
   (`Hot / Chilly / Variable`). Everything else is free prose in a ruled box.
