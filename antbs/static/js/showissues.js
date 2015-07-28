var l;
function handleResponse(response) {
    var output = "";
    var json = JSON.parse(response);
    var items = json.items;
    l = items.length;
    for (var i = 0; i < items.length; i++) {

        var row = document.createElement('tr');
        var issueId = document.createElement('td');
        issueId.innerHTML = '<a href=' + items[i].html_url + '\' target="_blanK">' + items[i].id + '</a>';
        var issueTitle = document.createElement('td');
        issueTitle.innerHTML = items[i].title;
        var issueState = document.createElement('td');
        issueState.innerHTML = items[i].state;
        var issueAssignee = document.createElement('td');
        var issueAssigneeUrl = items[i].assignee != null ? '<a href="' + items[i].assignee.html_url + '">' : '';
        issueAssignee.innerHTML = items[i].assignee != null ? issueAssigneeUrl + items[i].assignee.login + '</a>' : '';
        var issueMilestone = document.createElement('td');
        issueMilestone.innerHTML = items[i].milestone != null ? items[i].milestone.title : '';
        var issueUpdate = document.createElement('td');
        issueUpdate.innerHTML = items[i].updated_at;
        row.appendChild(issueId);
        row.appendChild(issueTitle);
        row.appendChild(issueState);
        row.appendChild(issueAssignee);
        row.appendChild(issueMilestone);
        row.appendChild(issueUpdate);
        document.getElementById('issue-table-body').appendChild(row);
    }
    console.log(items);
}

function progressListener() {
    if (this.readyState == 4 && this.status == 200) {
        handleResponse(this.responseText);
        var wmssg = document.getElementById('issuetable');
        if (l > 0) {
            jQuery('#issuetable').trigger('issuesReady');
        }
        else {
           // wmssg.innerHTML = 'No Issues Found !';
        }
    }
}
function showIssues() {
    //var wmssg = document.createElement('h1');
    //wmssg.innerHTML = "Fetching Github Issues ...";
    //wmssg.id = 'show_MSG';
    //document.getElementsByTagName('body')[0].appendChild(wmssg);
    //var projects = ["Addon-Tests", "Affiliates-Tests", "bouncer-tests", "marketplace-tests", "mcom-tests",
    //                "mdn-tests", "mozillians-tests", "moztrap-tests", "qmo-tests", "remo-tests", "snippets-tests",
    //                "Socorro-Tests", "sumo-tests", "wiki-tests"
    //];
    //for (var i = 0; i < projects.length; i++) {
    var apiURL = "https://api.github.com/search/issues?q=user:antergos+state:open&sort=created&order=asc";
    var client = new XMLHttpRequest();
    client.onreadystatechange = progressListener;
    client.open("GET", apiURL);
    client.setRequestHeader('Accept', 'application/json');
    client.setRequestHeader('Content-Type', 'application/json');
    client.send();

    //}
}
