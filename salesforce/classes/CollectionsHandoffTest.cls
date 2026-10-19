@IsTest
private class CollectionsHandoffTest {

    class ApiMock implements HttpCalloutMock {
        Integer status;
        ApiMock(Integer status) { this.status = status; }
        public HttpResponse respond(HttpRequest req) {
            HttpResponse r = new HttpResponse();
            r.setStatusCode(status);
            if (req.getEndpoint().endsWith('/summary')) {
                r.setBody('{"account_id":"L-50001","product_type":"AUTO","days_past_due":12,'
                    + '"past_due_amount":572.16,"late_fee":25.0,"amount_to_cure":597.16,'
                    + '"next_due_date":"2026-11-06","self_serve_allowed":true,"reason_code":""}');
            } else {
                r.setBody('{"account_id":"L-50001","eligible_codes":["PAY_NOW","PROMISE_TO_PAY"],"options":[]}');
            }
            return r;
        }
    }

    static CollectionsHandoff.Request req(String acct, String vid) {
        CollectionsHandoff.Request r = new CollectionsHandoff.Request();
        r.reason = 'member_request';
        r.summary = 'Member wants to talk about a payment plan longer than four weeks.';
        r.memberWords = 'Can I talk to a real person?';
        r.accountId = acct;
        r.verificationId = vid;
        return r;
    }

    @IsTest static void verifiedMemberGetsFactsFromSystemOfRecord() {
        Test.setMock(HttpCalloutMock.class, new ApiMock(200));
        Test.startTest();
        CollectionsHandoff.Result res = CollectionsHandoff.create(
            new List<CollectionsHandoff.Request>{ req('L-50001', 'v-123') })[0];
        Test.stopTest();
        Case c = [SELECT Subject, Description, Priority, Origin FROM Case LIMIT 1];
        System.assert(res.created);
        System.assert(c.Subject.contains('MEMBER_REQUEST - L-50001'));
        System.assert(c.Description.contains('$597.16 to bring current'));
        System.assert(c.Description.contains('PAY_NOW, PROMISE_TO_PAY'));
        System.assert(c.Description.contains('"Can I talk to a real person?"'));
        System.assertEquals('Medium', c.Priority);
    }

    @IsTest static void unverifiedMemberMakesNoCalloutAndIsFlagged() {
        CollectionsHandoff.Request r = req('L-50008', null);
        r.reason = 'HARDSHIP_REVIEW';
        CollectionsHandoff.create(new List<CollectionsHandoff.Request>{ r });
        Case c = [SELECT Description, Priority FROM Case LIMIT 1];
        System.assert(c.Description.contains('Identity verified: No'));
        System.assertEquals('High', c.Priority);
    }

    @IsTest static void apiFailureStillCreatesTheCase() {
        Test.setMock(HttpCalloutMock.class, new ApiMock(500));
        Test.startTest();
        CollectionsHandoff.Result res = CollectionsHandoff.create(
            new List<CollectionsHandoff.Request>{ req('L-50001', 'v-123') })[0];
        Test.stopTest();
        System.assert(res.created);
        System.assert([SELECT Description FROM Case LIMIT 1].Description.contains('Account facts unavailable'));
    }
}
