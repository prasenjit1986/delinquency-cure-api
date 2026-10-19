/**
 * Vera -> Collections Officer handoff.
 * Creates a Case in the Collections Officers queue so the officer never starts from zero.
 * Account facts come from the collections API (system of record), not from the AI.
 * Vera supplies only the reason, a summary and the member's own words.
 * If the API call fails, the case is still created and says so: the handoff must never be lost.
 */
public with sharing class CollectionsHandoff {

    public class Request {
        @InvocableVariable(label='Reason' description='One of: MEMBER_REQUEST, HARDSHIP_REVIEW, BANKRUPTCY, ATTORNEY, DISPUTE, POLICY_EXCEPTION, VERIFICATION_FAILED' required=true)
        public String reason;
        @InvocableVariable(label='Summary' description='Three to five plain sentences for the collections officer: what the member wants, what Vera offered or did, and anything the member said about their situation. No card or bank numbers.' required=true)
        public String summary;
        @InvocableVariable(label='Member Words' description='The member\'s request for a person, in their own words, copied exactly.')
        public String memberWords;
        @InvocableVariable(label='Account Id' description='Loan account number starting with L-, if known.')
        public String accountId;
        @InvocableVariable(label='Verification Id' description='Verification ID from the collections system, if the member is verified.')
        public String verificationId;
        @InvocableVariable(label='Messaging Session Id' description='The messaging session record ID, so the full transcript is linked to the case.')
        public String messagingSessionId;
    }

    public class Result {
        @InvocableVariable(label='Case Number') public String caseNumber;
        @InvocableVariable(label='Created') public Boolean created;
        @InvocableVariable(label='Message') public String message;
    }

    @TestVisible static final String QUEUE_NAME = 'Collections_Officers';

    @InvocableMethod(label='Create Collections Handoff'
        description='Creates a case for a collections officer with the account facts, Vera\'s summary and the member\'s words. Call this before handing the conversation to a person.'
        callout=true)
    public static List<Result> create(List<Request> requests) {
        List<Result> results = new List<Result>();
        for (Request r : requests) {
            results.add(createOne(r));
        }
        return results;
    }

    static Result createOne(Request r) {
        Result res = new Result();
        Boolean verified = String.isNotBlank(r.accountId) && String.isNotBlank(r.verificationId);

        // 1. Callouts first (Apex does not allow a callout after DML in the same transaction).
        Map<String, Object> summary;
        Map<String, Object> options;
        String factsError = '';
        if (verified) {
            try {
                summary = getJson('/v1/accounts/' + EncodingUtil.urlEncode(r.accountId, 'UTF-8') + '/summary', r.verificationId);
                options = getJson('/v1/accounts/' + EncodingUtil.urlEncode(r.accountId, 'UTF-8') + '/options', r.verificationId);
            } catch (Exception e) {
                factsError = 'Account facts unavailable (' + e.getMessage() + '). Check the core system.';
            }
        }

        // 2. Build the case.
        String reason = String.isBlank(r.reason) ? 'MEMBER_REQUEST' : r.reason.trim().toUpperCase();
        Boolean urgent = new Set<String>{'HARDSHIP_REVIEW', 'BANKRUPTCY', 'ATTORNEY', 'DISPUTE'}.contains(reason);

        Case c = new Case(
            Subject = 'Collections handoff: ' + reason + (String.isNotBlank(r.accountId) ? ' - ' + r.accountId : ''),
            Origin = 'Chat',
            Status = 'New',
            Priority = urgent ? 'High' : 'Medium',
            Description = buildDescription(r, reason, verified, summary, options, factsError)
        );
        Id queueId = queueId();
        if (queueId != null) c.OwnerId = queueId;
        insert c;

        // 3. Link the live transcript (production: the messaging session). Skipped in Preview.
        if (String.isNotBlank(r.messagingSessionId)) {
            try {
                // Dynamic so the class compiles even in orgs where Messaging is not enabled yet.
                SObject session = Schema.getGlobalDescribe().get('MessagingSession')
                    .newSObject(Id.valueOf(r.messagingSessionId));
                session.put('CaseId', c.Id);
                update session;
            } catch (Exception e) {
                // The case still stands; the session ID is in the description.
            }
        }

        res.caseNumber = [SELECT CaseNumber FROM Case WHERE Id = :c.Id].CaseNumber;
        res.created = true;
        res.message = 'Case ' + res.caseNumber + ' created for the collections team.';
        return res;
    }

    static String buildDescription(Request r, String reason, Boolean verified,
                                   Map<String, Object> s, Map<String, Object> o, String factsError) {
        List<String> lines = new List<String>();
        lines.add('HANDOFF FROM VERA (AI assistant)');
        lines.add('Reason: ' + reason);
        lines.add('Account: ' + (String.isBlank(r.accountId) ? 'not provided' : r.accountId));
        lines.add('Identity verified: ' + (verified ? 'Yes' : 'No - verify before discussing the account'));
        lines.add('');
        lines.add('ACCOUNT FACTS (from the collections system, not the AI)');
        if (s != null) {
            lines.add('Product: ' + s.get('product_type'));
            lines.add('Days past due: ' + s.get('days_past_due'));
            lines.add('Past due: $' + s.get('past_due_amount') + ' + late fee $' + s.get('late_fee')
                      + ' = $' + s.get('amount_to_cure') + ' to bring current');
            lines.add('Next due date: ' + s.get('next_due_date'));
            lines.add('Self-serve allowed: ' + s.get('self_serve_allowed')
                      + (String.isBlank((String) s.get('reason_code')) ? '' : ' (' + s.get('reason_code') + ')'));
        } else {
            lines.add(String.isNotBlank(factsError) ? factsError : 'Not available: member not verified.');
        }
        if (o != null) {
            List<String> eligible = new List<String>();
            for (Object code : (List<Object>) o.get('eligible_codes')) eligible.add(String.valueOf(code));
            lines.add('Options the policy allows today: ' + (eligible.isEmpty() ? 'none' : String.join(eligible, ', ')));
        }
        lines.add('');
        lines.add('MEMBER\'S WORDS');
        lines.add(String.isBlank(r.memberWords) ? '(not captured)' : '"' + r.memberWords + '"');
        lines.add('');
        lines.add('VERA\'S SUMMARY');
        lines.add(String.isBlank(r.summary) ? '(none)' : r.summary);
        lines.add('');
        lines.add('Transcript: ' + (String.isBlank(r.messagingSessionId)
            ? 'not linked (test conversation)' : 'Messaging Session ' + r.messagingSessionId));
        return String.join(lines, '\n').left(32000);
    }

    static Map<String, Object> getJson(String path, String verificationId) {
        HttpRequest req = new HttpRequest();
        req.setEndpoint('callout:CollectionsAPI' + path);
        req.setMethod('GET');
        req.setHeader('X-Verification-Id', verificationId);
        req.setTimeout(20000);
        HttpResponse resp = new Http().send(req);
        if (resp.getStatusCode() != 200) {
            throw new CalloutException('HTTP ' + resp.getStatusCode());
        }
        return (Map<String, Object>) JSON.deserializeUntyped(resp.getBody());
    }

    static Id queueId() {
        List<Group> q = [SELECT Id FROM Group WHERE Type = 'Queue' AND DeveloperName = :QUEUE_NAME LIMIT 1];
        return q.isEmpty() ? null : q[0].Id;
    }
}
