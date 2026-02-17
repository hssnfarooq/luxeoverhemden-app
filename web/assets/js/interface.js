
function createAvatarItem(avatarId, avatarName, avatarImage, avatarStatus, avatarLastSeen, avatarTitle, avatarMail, pictures, entityName, proxy, language) {
    if (avatarStatus != 'online' && avatarStatus != 'offline' && avatarStatus != 'away') {
      avatarStatus = 'offline';
    }

    const li = document.createElement('li');
    li.classList.add('chat-item', 'border', 'border--gray', 'nav-item', 'w-100', 'rounded', 'mb-3');
    li.dataset.name = avatarName;

    const a = document.createElement('a');
    a.classList.add('nav-link', 'active', 'd-flex', 'justify-content-between', 'p-3');
    a.style.cursor = 'pointer';

    const div1 = document.createElement('div');
    div1.classList.add('d-flex');

    const div2 = document.createElement('div');
    div2.classList.add('position-relative');

    const img = document.createElement('img');
    img.src = avatarImage;
    img.alt = avatarName;
    img.classList.add('rounded-circle', 'me-3', 'hw-40', 'img-fluid');

    const span = document.createElement('span');
    span.classList.add('status', avatarStatus, 'position-absolute');

    div2.appendChild(img);
    div2.appendChild(span);

    const div3 = document.createElement('div');
    div3.classList.add('chat-content');

    const h3 = document.createElement('h3');
    h3.textContent = avatarName;

    const p = document.createElement('p');
    p.textContent = avatarTitle;

    div3.appendChild(h3);
    div3.appendChild(p);

    div1.appendChild(div2);
    div1.appendChild(div3);

    const div4 = document.createElement('div');
    div4.classList.add('chat-time');

    const h3_2 = document.createElement('h3');
    h3_2.classList.add('text--gray-200');
    h3_2.textContent = avatarLastSeen;

    div4.appendChild(h3_2);

    a.appendChild(div1);
    a.appendChild(div4);

    li.appendChild(a);

    li.addEventListener('click', function (e) {
            console.log('clicked chat-item');
            tabNavigation('profile-tab');
            // set the values of the profile tab
            document.querySelector('#avatar-header-name').textContent = avatarName;
            document.querySelector('#avatar-header-image').src = avatarImage;
            document.querySelector('#avatar-details-name').textContent = avatarName;
            document.querySelector('#avatar-details-email').textContent = avatarMail;
            document.querySelector('#avatar-details-email').href = 'mailto:'+avatarMail;
            document.getElementById('browserbutton').setAttribute('data-id', avatarId);
            document.getElementById('browserbutton').setAttribute('data-proxy', proxy);
            document.getElementById('browserbutton').setAttribute('data-language', language);
            document.getElementById('avatar-entity-name').innerHTML=entityName

            // set the pictures and hide the undefined ones
            for (let i = 0; i < pictures.length; i++) {
              document.getElementById('avatar-picture-'+i).src = pictures[i];
              document.getElementById('avatar-picture-'+i).style.display = 'block';
            }
            for (let i = pictures.length; i < 4; i++) {
              document.getElementById('avatar-picture-'+i).style.display = 'none';
            }
            
            

            $('.chat-item').removeClass("open");
            $(this).addClass("open");
          });

    $('#avatar-list').append(li);
  }
  

  function add_contact_to_list(contactId, contactName, contactImage='', contactEmail='', contactLocation='') {
    // create the container element
    const container = document.createElement("div");
    container.classList.add("position-relative", "d-flex", "align-items-center", "mb-3", "contact_details_container");
    container.setAttribute("data-id", contactId);
    container.setAttribute("data-name", contactName);
  
    // create the link element
    const link = document.createElement("a");
    link.classList.add("text--dark-100", "font-weight--500", "font-size--16", "contact-details");
    link.href = "#";
    link.textContent = contactName;
    link.onclick = function() {
      tabNavigation('contact-tab');
      document.getElementById('contact-header-name').innerHTML=contactName
      document.getElementById('contact-header-image').src=contactImage
      document.getElementById('contact-details-name').innerHTML=contactName
      document.getElementById('contact-details-email').innerHTML=contactEmail
      document.getElementById('contact-details-email').href='mailto:'+contactEmail
      document.getElementById('contact-details-location').innerHTML=contactLocation
      document.getElementById('avatarOptionsClearBrowser').setAttribute('data-id', contactId);
    }
    // add onclick for the link

  
    // add the link element to the container element
    container.appendChild(link);
  
    $('li[data-group="'+contactName.charAt(0)+'"]').append(container);
  }
